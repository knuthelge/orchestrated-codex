"""Installer tests for the two-root layout (agents under codex-home, skill under $HOME)."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from codex_orchestrator import cli
from codex_orchestrator.sources import GitHubSourceResolver


class _TTYOutput(io.StringIO):
    """Capture terminal writes while reporting itself as an interactive stream."""

    def __init__(self, events: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self.events = events

    def isatty(self) -> bool:
        return True

    def write(self, value: str) -> int:
        if self.events is not None:
            self.events.append(("stdout", value))
        return super().write(value)


class _RecordingError(io.StringIO):
    """Capture stderr writes in the same timeline as a fake terminal."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        super().__init__()
        self.events = events

    def write(self, value: str) -> int:
        self.events.append(("stderr", value))
        return super().write(value)


class _ControlledSpinnerEvent:
    """Advance exactly one spinner frame when the resolver asks for it."""

    def __init__(self) -> None:
        self.stopped = False
        self.wait_calls = 0

    def wait(self, timeout: float) -> bool:
        self.wait_calls += 1
        return self.stopped or self.wait_calls > 1

    def set(self) -> None:
        self.stopped = True


class _ControlledSpinnerThread:
    """Run the spinner deterministically from the resolver without waiting."""

    def __init__(self, target: object, daemon: bool) -> None:
        self.target = target
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def advance(self) -> None:
        self.target()  # type: ignore[operator]

    def join(self) -> None:
        self.joined = True


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        base = Path(self.temporary_directory.name)
        self.agents_root = base / "codex-home"
        self.skill_root = base / "home" / ".agents" / "skills"
        self.agents_root.mkdir(parents=True)
        self.skill_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def files(self) -> dict[Path, Path]:
        return cli.source_files(self.agents_root, self.skill_root)

    def captured_install(self) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli.install(self.agents_root, self.skill_root)
        return result, output.getvalue()

    @staticmethod
    def materialize_remote(
        destination_root: Path, paths: list[Path] | tuple[Path, ...], sha: str = "a" * 40
    ) -> SimpleNamespace:
        """Make a resolver result with distinct explicitly requested files."""
        result_paths: dict[Path, Path] = {}
        for source_path in paths:
            target = destination_root / source_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"remote file: {source_path.as_posix()} @ {sha}", encoding="utf-8"
            )
            result_paths[source_path] = target
        return SimpleNamespace(sha=sha, paths=result_paths)

    def remote_resolver(self, sha: str = "a" * 40) -> unittest.mock.Mock:
        resolver = unittest.mock.Mock()

        def materialize(repository: object, paths: list[Path], root: Path) -> SimpleNamespace:
            return self.materialize_remote(root, paths, sha)

        resolver.materialize.side_effect = materialize
        return resolver

    def test_install_places_skill_and_agents_in_independent_roots(self) -> None:
        expected = self.files()

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["files"]), {path.as_posix() for path in expected})
        for destination, source in expected.items():
            self.assertEqual(destination.read_bytes(), source.read_bytes())

        # SC-1: skills land directly under $HOME/.agents/skills.
        delivery_skill_dir = self.skill_root / "orchestrated-delivery"
        self.assertTrue((delivery_skill_dir / "SKILL.md").is_file())
        self.assertTrue((delivery_skill_dir / "agents" / "openai.yaml").is_file())
        self.assertTrue((delivery_skill_dir / "references").is_dir())
        review_skill_dir = self.skill_root / "code-review"
        self.assertTrue((review_skill_dir / "SKILL.md").is_file())
        self.assertTrue((review_skill_dir / "agents" / "openai.yaml").is_file())
        self.assertTrue(
            (review_skill_dir / "references" / "review-checklist-template.md").is_file()
        )

        # SC-2: agents land under <codex-home>/agents.
        for name in (
            "discovery.toml",
            "spec-designer.toml",
            "rubber-duck.toml",
            "ui-designer.toml",
            "tester.toml",
            "final-reviewer.toml",
        ):
            self.assertTrue((self.agents_root / "agents" / name).is_file())
        self.assertFalse((self.agents_root / "agents" / "verifier.toml").exists())

    def test_directory_symlink_installation_roots_support_install_reinstall_and_uninstall(self) -> None:
        for linked_root in ("codex-home", "skills"):
            with self.subTest(linked_root=linked_root), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                agents_root = base / "codex-home"
                skill_root = base / "home" / ".agents" / "skills"
                agents_root.parent.mkdir(parents=True, exist_ok=True)
                skill_root.parent.mkdir(parents=True, exist_ok=True)
                if linked_root == "codex-home":
                    target = base / "actual-codex-home"
                    target.mkdir()
                    agents_root.symlink_to(target, target_is_directory=True)
                    skill_root.mkdir()
                else:
                    agents_root.mkdir()
                    target = base / "actual-skills"
                    target.mkdir()
                    skill_root.symlink_to(target, target_is_directory=True)

                self.assertEqual(cli.install(agents_root, skill_root), 0)
                self.assertEqual(cli.install(agents_root, skill_root), 0)
                self.assertTrue((agents_root / "agents" / "discovery.toml").is_file())
                self.assertTrue((skill_root / "code-review" / "SKILL.md").is_file())
                self.assertEqual(cli.uninstall(agents_root, skill_root), 0)
                self.assertTrue((agents_root if linked_root == "codex-home" else skill_root).is_symlink())

    def test_install_reports_checksum_based_file_changes(self) -> None:
        source = Path(self.temporary_directory.name) / "source.toml"
        destination = self.agents_root / "agents" / "example.toml"
        source.write_text("version 1", encoding="utf-8")
        component = cli.default_registry().components[0]

        with unittest.mock.patch.object(
            cli,
            "source_plan",
            return_value=(
                {destination: source},
                {destination: {component.id}},
                (component,),
            ),
        ):
            result, output = self.captured_install()
            self.assertEqual(result, 0)
            self.assertIn(
                "Changes:\n"
                "  Added     1\n"
                "    + agents/example.toml\n"
                "  Updated   0\n"
                "  Removed   0\n"
                "  Unchanged 0\n"
                "  Preserved 0\n",
                output,
            )

            result, output = self.captured_install()
            self.assertEqual(result, 0)
            self.assertIn(
                "  Added     0\n"
                "  Updated   0\n"
                "  Removed   0\n"
                "  Unchanged 1\n"
                "  Preserved 0\n",
                output,
            )

            source.write_text("version 2", encoding="utf-8")
            result, output = self.captured_install()
            self.assertEqual(result, 0)
            self.assertIn("  Updated   1\n    ~ agents/example.toml\n", output)
            self.assertEqual(destination.read_text(encoding="utf-8"), "version 2")

        with unittest.mock.patch.object(
            cli,
            "source_plan",
            return_value=({}, {}, (component,)),
        ):
            result, output = self.captured_install()
            self.assertEqual(result, 0)
            self.assertIn("  Removed   1\n    - agents/example.toml\n", output)

    def test_uninstall_removes_files_from_both_roots(self) -> None:
        expected = self.files()
        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 0)

        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())
        for destination in expected:
            self.assertFalse(destination.exists())
        self.assertFalse((self.skill_root / "orchestrated-delivery").exists())
        self.assertFalse((self.skill_root / "code-review").exists())

    def test_install_refuses_foreign_file(self) -> None:
        destination = next(iter(self.files()))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("user content", encoding="utf-8")

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "user content")
        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_install_refuses_foreign_skill_file(self) -> None:
        destination = self.skill_root / "orchestrated-delivery" / "SKILL.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("user skill", encoding="utf-8")

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "user skill")
        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_preserves_modified_file_and_manifest(self) -> None:
        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)
        destination = next(iter(self.files()))
        destination.write_text("locally modified", encoding="utf-8")

        self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "locally modified")
        self.assertTrue((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_rejects_matching_absolute_path_outside_installation_roots(
        self,
    ) -> None:
        victim = self.agents_root.parent / "unrelated-file"
        victim.write_text("do not delete", encoding="utf-8")
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 2,
                    "files": {
                        victim.as_posix(): hashlib.sha256(
                            victim.read_bytes()
                        ).hexdigest()
                    },
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "outside installation roots"):
            cli.uninstall(self.agents_root, self.skill_root)

        self.assertEqual(victim.read_text(encoding="utf-8"), "do not delete")
        self.assertTrue((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_reinstall_rejects_matching_absolute_obsolete_path_outside_roots(
        self,
    ) -> None:
        victim = self.agents_root.parent / "obsolete-unrelated-file"
        victim.write_text("do not delete", encoding="utf-8")
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 2,
                    "files": {
                        victim.as_posix(): hashlib.sha256(
                            victim.read_bytes()
                        ).hexdigest()
                    },
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "outside installation roots"):
            cli.install(self.agents_root, self.skill_root)

        self.assertEqual(victim.read_text(encoding="utf-8"), "do not delete")
        self.assertTrue((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_supports_current_absolute_entries_under_both_roots(self) -> None:
        agent_file = self.agents_root / "agents" / "owned.toml"
        skill_file = self.skill_root / "owned-skill" / "SKILL.md"
        agent_file.parent.mkdir(parents=True)
        skill_file.parent.mkdir(parents=True)
        agent_file.write_text("owned agent", encoding="utf-8")
        skill_file.write_text("owned skill", encoding="utf-8")
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 2,
                    "files": {
                        agent_file.as_posix(): hashlib.sha256(
                            agent_file.read_bytes()
                        ).hexdigest(),
                        skill_file.as_posix(): hashlib.sha256(
                            skill_file.read_bytes()
                        ).hexdigest(),
                    },
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 0)

        self.assertFalse(agent_file.exists())
        self.assertFalse(skill_file.exists())
        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_rejects_relative_traversal_and_prefix_confusion(self) -> None:
        traversal_victim = self.agents_root.parent / "traversal-victim"
        prefix_victim = (
            self.agents_root.parent / f"{self.agents_root.name}-other" / "victim"
        )
        traversal_victim.write_text("do not delete", encoding="utf-8")
        prefix_victim.parent.mkdir()
        prefix_victim.write_text("do not delete", encoding="utf-8")

        for key, victim in (
            ("../traversal-victim", traversal_victim),
            (prefix_victim.as_posix(), prefix_victim),
        ):
            with self.subTest(key=key):
                (self.agents_root / cli.MANIFEST_NAME).write_text(
                    json.dumps(
                        {
                            "installer": "codex-orchestrator",
                            "version": 2,
                            "files": {
                                key: hashlib.sha256(victim.read_bytes()).hexdigest()
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(
                    RuntimeError, "Unsafe path|outside installation roots"
                ):
                    cli.uninstall(self.agents_root, self.skill_root)

                self.assertEqual(victim.read_text(encoding="utf-8"), "do not delete")
                self.assertTrue((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_rejects_path_escaping_via_symlinked_parent(self) -> None:
        outside_directory = self.agents_root.parent / "outside"
        victim = outside_directory / "victim"
        outside_directory.mkdir()
        victim.write_text("do not delete", encoding="utf-8")
        link = self.agents_root / "linked-agents"
        link.symlink_to(outside_directory, target_is_directory=True)
        escaped_path = link / victim.name
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 2,
                    "files": {
                        escaped_path.as_posix(): hashlib.sha256(
                            victim.read_bytes()
                        ).hexdigest()
                    },
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "outside installation roots"):
            cli.uninstall(self.agents_root, self.skill_root)

        self.assertEqual(victim.read_text(encoding="utf-8"), "do not delete")
        self.assertTrue((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_uninstall_supports_legacy_relative_entry_under_agents_root(self) -> None:
        owned_file = self.agents_root / "agents" / "legacy-owned.toml"
        owned_file.parent.mkdir(parents=True)
        owned_file.write_text("legacy owned", encoding="utf-8")
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 1,
                    "files": {
                        "agents/legacy-owned.toml": hashlib.sha256(
                            owned_file.read_bytes()
                        ).hexdigest()
                    },
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 0)

        self.assertFalse(owned_file.exists())
        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_reinstall_removes_unchanged_obsolete_file(self) -> None:
        obsolete = self.agents_root / "agents" / "obsolete.toml"
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("old", encoding="utf-8")
        recorded = hashlib.sha256(obsolete.read_bytes()).hexdigest()
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 1,
                    "files": {"agents/obsolete.toml": recorded},
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)
        self.assertFalse(obsolete.exists())

    def test_reinstall_removes_legacy_verifier_and_legacy_skill(self) -> None:
        # Simulate a prior install: verifier.toml plus the skill under <codex-home>/skills.
        legacy_verifier = self.agents_root / "agents" / "verifier.toml"
        legacy_skill = (
            self.agents_root / "skills" / "orchestrated-delivery" / "SKILL.md"
        )
        legacy_verifier.parent.mkdir(parents=True, exist_ok=True)
        legacy_skill.parent.mkdir(parents=True, exist_ok=True)
        legacy_verifier.write_text("old verifier", encoding="utf-8")
        legacy_skill.write_text("old skill", encoding="utf-8")
        manifest = {
            "installer": "codex-orchestrator",
            "version": 1,
            "files": {
                "agents/verifier.toml": hashlib.sha256(
                    legacy_verifier.read_bytes()
                ).hexdigest(),
                "skills/orchestrated-delivery/SKILL.md": hashlib.sha256(
                    legacy_skill.read_bytes()
                ).hexdigest(),
            },
        }
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        self.assertFalse(legacy_verifier.exists())
        self.assertFalse(legacy_skill.exists())
        self.assertTrue(
            (self.skill_root / "orchestrated-delivery" / "SKILL.md").is_file()
        )

    def test_reinstall_preserves_modified_legacy_skill(self) -> None:
        legacy_skill = (
            self.agents_root / "skills" / "orchestrated-delivery" / "SKILL.md"
        )
        legacy_skill.parent.mkdir(parents=True, exist_ok=True)
        legacy_skill.write_text("recorded", encoding="utf-8")
        recorded = hashlib.sha256(legacy_skill.read_bytes()).hexdigest()
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 1,
                    "files": {"skills/orchestrated-delivery/SKILL.md": recorded},
                }
            ),
            encoding="utf-8",
        )
        legacy_skill.write_text("locally modified legacy", encoding="utf-8")

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)
        self.assertEqual(
            legacy_skill.read_text(encoding="utf-8"), "locally modified legacy"
        )

    def test_foreign_manifest_is_rejected(self) -> None:
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps({"installer": "some-other-tool", "files": {}}),
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            cli.install(self.agents_root, self.skill_root)

    def test_main_resolves_roots_from_environment(self) -> None:
        base = Path(self.temporary_directory.name)
        home = base / "env-home"
        codex_home = base / "env-codex"
        home.mkdir()
        env = {"HOME": str(home), "CODEX_HOME": str(codex_home)}
        with unittest.mock.patch.dict("os.environ", env, clear=False):
            self.assertEqual(cli.main(["--install", "--all"]), 0)

        self.assertTrue(
            (
                home / ".agents" / "skills" / "orchestrated-delivery" / "SKILL.md"
            ).is_file()
        )
        self.assertTrue(
            (home / ".agents" / "skills" / "code-review" / "SKILL.md").is_file()
        )
        self.assertTrue((codex_home / "agents" / "tester.toml").is_file())

    def test_install_can_select_only_code_review(self) -> None:
        self.assertEqual(
            cli.install(self.agents_root, self.skill_root, ("code-review",)), 0
        )

        self.assertTrue((self.skill_root / "code-review" / "SKILL.md").is_file())
        self.assertFalse((self.skill_root / "orchestrated-delivery").exists())
        self.assertFalse((self.agents_root / "agents").exists())
        manifest = json.loads(
            (self.agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], 4)
        self.assertEqual(manifest["components"], ["code-review"])
        self.assertTrue(manifest["file_owners"])
        self.assertEqual(set(manifest["file_provenance"]), set(manifest["files"]))
        self.assertTrue(
            all(
                entry["kind"] == "bundled"
                and set(entry) == {"kind", "path"}
                for entry in manifest["file_provenance"].values()
            )
        )
        self.assertTrue(
            all(
                owners == ["code-review"] for owners in manifest["file_owners"].values()
            )
        )

    def test_new_selection_removes_unchanged_deselected_component(self) -> None:
        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        self.assertEqual(
            cli.install(self.agents_root, self.skill_root, ("code-review",)), 0
        )

        self.assertFalse((self.skill_root / "orchestrated-delivery").exists())
        self.assertFalse((self.agents_root / "agents").exists())
        self.assertTrue((self.skill_root / "code-review" / "SKILL.md").is_file())

    def test_new_selection_preserves_modified_deselected_component(self) -> None:
        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)
        modified = self.skill_root / "orchestrated-delivery" / "SKILL.md"
        modified.write_text("locally modified", encoding="utf-8")

        self.assertEqual(
            cli.install(self.agents_root, self.skill_root, ("code-review",)), 0
        )

        self.assertEqual(modified.read_text(encoding="utf-8"), "locally modified")
        manifest = json.loads(
            (self.agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        self.assertIn(modified.as_posix(), manifest["files"])
        self.assertEqual(manifest["components"], ["code-review"])

    def test_main_installs_named_component_without_tty(self) -> None:
        base = Path(self.temporary_directory.name)
        home = base / "named-home"
        codex_home = base / "named-codex"
        home.mkdir()
        with unittest.mock.patch.dict(
            "os.environ",
            {"HOME": str(home), "CODEX_HOME": str(codex_home)},
            clear=False,
        ):
            self.assertEqual(cli.main(["--install", "--components", "code-review"]), 0)

        self.assertTrue((home / ".agents/skills/code-review/SKILL.md").is_file())
        self.assertFalse((home / ".agents/skills/orchestrated-delivery").exists())
        self.assertFalse((codex_home / "agents").exists())

    def test_plain_install_requires_tty(self) -> None:
        error = io.StringIO()
        with (
            unittest.mock.patch.object(
                cli.sys, "stdin", unittest.mock.Mock(isatty=lambda: False)
            ),
            unittest.mock.patch.object(
                cli.sys, "stdout", unittest.mock.Mock(isatty=lambda: False)
            ),
            unittest.mock.patch.object(cli.sys, "stderr", error),
        ):
            self.assertEqual(cli.main(["--install"]), 1)
        self.assertIn("use --all or --components", error.getvalue())

    def test_component_selector_preselects_installed_choices(self) -> None:
        registry = cli.default_registry()
        prompt = unittest.mock.Mock()
        prompt.application.layout.find_all_controls.return_value = ()
        prompt.ask.return_value = ["orchestrated-delivery"]
        with (
            unittest.mock.patch.object(
                cli.sys, "stdin", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.sys, "stdout", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.questionary, "checkbox", return_value=prompt
            ) as checkbox,
        ):
            selected = cli.select_components(registry, {"code-review"})

        self.assertEqual(selected, ("orchestrated-delivery",))
        choices = checkbox.call_args.kwargs["choices"]
        separators = [
            choice
            for choice in choices
            if isinstance(choice, cli.questionary.Separator)
        ]
        self.assertEqual(len(separators), 1)
        self.assertIs(choices[2], separators[0])
        self.assertEqual(separators[0].title, "── Third-party ──")
        self.assertTrue(separators[0].disabled)
        selectable_choices = [
            choice
            for choice in choices
            if not isinstance(choice, cli.questionary.Separator)
        ]
        self.assertFalse(selectable_choices[0].checked)
        self.assertTrue(selectable_choices[1].checked)
        rendered_titles = [
            "".join(fragment for _, fragment in choice.title)
            for choice in selectable_choices
        ]
        self.assertEqual(
            rendered_titles,
            [
                "Orchestrated delivery - Skill + 6 custom agents.",
                "Code review - Skill + supporting references.",
                "Grill me - 2 skills + agent metadata. (3rd party)",
                "Handoff - Skill + agent metadata. (3rd party)",
                "Teach - Skill + 4 format guides + agent metadata. (3rd party)",
            ],
        )
        self.assertIs(checkbox.call_args.kwargs["style"], cli.INSTALLER_STYLE)
        self.assertIs(
            checkbox.call_args.kwargs["validate"](["orchestrated-delivery"]), True
        )

    def test_component_selector_hides_the_terminal_cursor(self) -> None:
        prompt = cli.questionary.checkbox(
            "Select components to install",
            choices=[cli.questionary.Choice("Example", checked=True)],
        )

        controls = tuple(prompt.application.layout.find_all_controls())
        cli._hide_prompt_cursor(prompt)

        formatted_controls = [
            control
            for control in controls
            if isinstance(control, cli.FormattedTextControl)
        ]
        self.assertTrue(formatted_controls)
        self.assertTrue(all(not control.show_cursor for control in formatted_controls))

    def test_component_selector_uses_foreground_only_indicator_styles(self) -> None:
        selected = cli.INSTALLER_STYLE.get_attrs_for_style_str("class:selected")
        unselected = cli.INSTALLER_STYLE.get_attrs_for_style_str("class:text")

        self.assertEqual(selected.color, "ansigreen")
        self.assertFalse(selected.reverse)
        self.assertEqual(unselected.color, "ansiwhite")
        self.assertFalse(unselected.reverse)

    def test_component_selection_requires_dependencies_before_submit(self) -> None:
        registry = cli.default_registry()
        dependent_review = replace(
            registry.by_id["code-review"],
            depends_on=("orchestrated-delivery",),
        )
        registry = type(registry)(
            (registry.by_id["orchestrated-delivery"], dependent_review),
            registry.resource_root,
        )

        invalid = cli.validate_component_selection(registry, ["code-review"])
        valid = cli.validate_component_selection(
            registry, ["orchestrated-delivery", "code-review"]
        )

        self.assertEqual(invalid, "Select required dependencies: Orchestrated delivery")
        self.assertIs(valid, True)

    def test_selector_hides_new_inactive_component(self) -> None:
        registry = cli.default_registry()
        inactive_review = replace(registry.by_id["code-review"], active=False)
        registry = type(registry)(
            (registry.by_id["orchestrated-delivery"], inactive_review),
            registry.resource_root,
        )
        prompt = unittest.mock.Mock()
        prompt.application.layout.find_all_controls.return_value = ()
        prompt.ask.return_value = ["orchestrated-delivery"]
        with (
            unittest.mock.patch.object(
                cli.sys, "stdin", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.sys, "stdout", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.questionary, "checkbox", return_value=prompt
            ) as checkbox,
        ):
            selected = cli.select_components(registry, set())

        self.assertEqual(selected, ("orchestrated-delivery",))
        choices = checkbox.call_args.kwargs["choices"]
        self.assertEqual(
            [choice.value for choice in choices], ["orchestrated-delivery"]
        )

    def test_selector_shows_installed_inactive_component_as_retired(self) -> None:
        registry = cli.default_registry()
        inactive_review = replace(registry.by_id["code-review"], active=False)
        registry = type(registry)(
            (registry.by_id["orchestrated-delivery"], inactive_review),
            registry.resource_root,
        )
        prompt = unittest.mock.Mock()
        prompt.application.layout.find_all_controls.return_value = ()
        prompt.ask.return_value = ["code-review"]
        with (
            unittest.mock.patch.object(
                cli.sys, "stdin", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.sys, "stdout", unittest.mock.Mock(isatty=lambda: True)
            ),
            unittest.mock.patch.object(
                cli.questionary, "checkbox", return_value=prompt
            ) as checkbox,
        ):
            selected = cli.select_components(registry, {"code-review"})

        self.assertEqual(selected, ("code-review",))
        choices = checkbox.call_args.kwargs["choices"]
        self.assertEqual(len(choices), 2)
        rendered_title = "".join(fragment for _, fragment in choices[1].title)
        self.assertIn("retired; deselect to remove", rendered_title)
        self.assertTrue(choices[1].checked)

    def test_all_excludes_inactive_components_and_named_install_rejects_them(
        self,
    ) -> None:
        registry = cli.default_registry()
        inactive_review = replace(registry.by_id["code-review"], active=False)
        registry = type(registry)(
            (registry.by_id["orchestrated-delivery"], inactive_review),
            registry.resource_root,
        )
        base = Path(self.temporary_directory.name)
        home = base / "active-home"
        codex_home = base / "active-codex"
        home.mkdir()
        environment = {"HOME": str(home), "CODEX_HOME": str(codex_home)}
        with (
            unittest.mock.patch.dict("os.environ", environment, clear=False),
            unittest.mock.patch.object(cli, "default_registry", return_value=registry),
        ):
            self.assertEqual(cli.main(["--install", "--all"]), 0)
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(
                    cli.main(["--install", "--components", "code-review"]), 2
                )

        self.assertTrue(
            (home / ".agents/skills/orchestrated-delivery/SKILL.md").is_file()
        )
        self.assertFalse((home / ".agents/skills/code-review").exists())
        self.assertIn("argument --components: inactive component ID", error.getvalue())

    def test_unknown_named_component_is_an_argument_error(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_orchestrator",
                "--install",
                "--components",
                "code-reveiw",
                "--codex-home",
                str(self.agents_root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        self.assertIn(
            "argument --components: unknown component ID(s): code-reveiw",
            result.stderr,
        )
        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())

    def test_grill_me_installs_companion_and_deselect_preserves_modified_companion(
        self,
    ) -> None:
        resolver = self.remote_resolver()
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", return_value=resolver
        ):
            self.assertEqual(
                cli.install(self.agents_root, self.skill_root, ("grill-me",)), 0
            )

        grill = self.skill_root / "grill-me" / "SKILL.md"
        grilling = self.skill_root / "grilling" / "SKILL.md"
        self.assertTrue(grill.is_file())
        self.assertTrue(grilling.is_file())
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["components"], ["grill-me"])
        for path in (grill, grilling):
            provenance = manifest["file_provenance"][path.as_posix()]
            self.assertEqual(provenance["kind"], "github")
            self.assertEqual(provenance["sha"], "a" * 40)
            self.assertEqual(
                set(provenance), {"kind", "path", "owner", "repository", "ref", "sha"}
            )

        grilling.write_text("locally changed companion", encoding="utf-8")
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", side_effect=AssertionError("network used")
        ):
            self.assertEqual(
                cli.install(self.agents_root, self.skill_root, ("code-review",)), 0
            )

        self.assertFalse(grill.exists())
        self.assertEqual(grilling.read_text(encoding="utf-8"), "locally changed companion")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["components"], ["code-review"])
        self.assertEqual(
            manifest["file_provenance"][grilling.as_posix()],
            {
                "kind": "github",
                "path": "skills/productivity/grilling/SKILL.md",
                "owner": "mattpocock",
                "repository": "skills",
                "ref": "main",
                "sha": "a" * 40,
            },
        )

    def test_bundled_all_is_network_free_and_retains_installed_remote_state(self) -> None:
        resolver = self.remote_resolver()
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", return_value=resolver
        ):
            self.assertEqual(cli.install(self.agents_root, self.skill_root, ("handoff",)), 0)
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        before = json.loads(manifest_path.read_text(encoding="utf-8"))
        remote_paths = {
            key for key, entry in before["file_provenance"].items()
            if entry["kind"] == "github"
        }
        self.assertTrue(remote_paths)

        home = self.skill_root.parent.parent
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(
                cli, "GitHubSourceResolver", side_effect=AssertionError("network used")
            ),
        ):
            self.assertEqual(cli.main(["--install", "--all"]), 0)

        after = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("handoff", after["components"])
        self.assertEqual(
            {key: after["files"][key] for key in remote_paths},
            {key: before["files"][key] for key in remote_paths},
        )
        self.assertEqual(
            {key: after["file_owners"][key] for key in remote_paths},
            {key: before["file_owners"][key] for key in remote_paths},
        )
        self.assertEqual(
            {key: after["file_provenance"][key] for key in remote_paths},
            {key: before["file_provenance"][key] for key in remote_paths},
        )

    def test_explicit_remote_component_and_include_third_party_materialize_requested_sources(
        self,
    ) -> None:
        home = self.skill_root.parent.parent
        resolver = self.remote_resolver()
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(
                cli, "GitHubSourceResolver", return_value=resolver
            ),
        ):
            self.assertEqual(cli.main(["--install", "--components", "handoff"]), 0)
            self.assertEqual(cli.main(["--install", "--all", "--include-third-party"]), 0)

        requested = [
            set(call.args[1]) for call in resolver.materialize.call_args_list
        ]
        self.assertIn({
            Path("skills/productivity/handoff/SKILL.md"),
            Path("skills/productivity/handoff/agents/openai.yaml"),
        }, requested)
        self.assertIn(
            {
                Path("skills/productivity/grill-me/SKILL.md"),
                Path("skills/productivity/grill-me/agents/openai.yaml"),
                Path("skills/productivity/grilling/SKILL.md"),
                Path("skills/productivity/grilling/agents/openai.yaml"),
                Path("skills/productivity/handoff/SKILL.md"),
                Path("skills/productivity/handoff/agents/openai.yaml"),
                Path("skills/productivity/teach/SKILL.md"),
                Path("skills/productivity/teach/GLOSSARY-FORMAT.md"),
                Path("skills/productivity/teach/LEARNING-RECORD-FORMAT.md"),
                Path("skills/productivity/teach/MISSION-FORMAT.md"),
                Path("skills/productivity/teach/RESOURCES-FORMAT.md"),
                Path("skills/productivity/teach/agents/openai.yaml"),
            },
            requested,
        )

    def test_direct_remote_files_reach_destinations_with_commit_provenance(self) -> None:
        commit = "b" * 40
        requests: list[str] = []

        def fetch(url: str, maximum: int, host: str, deadline: float, accept: str) -> bytes:
            requests.append(url)
            if host == "api.github.com":
                return json.dumps({
                    "ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": commit},
                }).encode()
            return f"contents of {url.rsplit('/', 1)[-1]}\n".encode()

        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver",
            side_effect=lambda: GitHubSourceResolver(fetch=fetch),
        ):
            self.assertEqual(cli.install(self.agents_root, self.skill_root, ("handoff",)), 0)

        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0], "https://api.github.com/repos/mattpocock/skills/git/ref/heads/main")
        skill = self.skill_root / "handoff" / "SKILL.md"
        agent = self.skill_root / "handoff" / "agents" / "openai.yaml"
        self.assertEqual(skill.read_text(encoding="utf-8"), "contents of SKILL.md\n")
        self.assertEqual(agent.read_text(encoding="utf-8"), "contents of openai.yaml\n")
        manifest = json.loads((self.agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["file_provenance"][skill.as_posix()]["sha"], commit)
        self.assertEqual(
            manifest["file_provenance"][agent.as_posix()]["path"],
            "skills/productivity/handoff/agents/openai.yaml",
        )

    def test_remote_preflight_failure_leaves_bundled_files_and_manifest_unchanged(self) -> None:
        self.assertEqual(cli.install(self.agents_root, self.skill_root, ("orchestrated-delivery",)), 0)
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        before_manifest = manifest_path.read_bytes()
        before_files = {
            path: path.read_bytes()
            for path in cli.source_files(
                self.agents_root, self.skill_root, ("orchestrated-delivery",)
            )
        }
        resolver = unittest.mock.Mock()
        resolver.materialize.side_effect = cli.SourceError("DNS failure")
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", return_value=resolver
        ):
            with self.assertRaisesRegex(cli.SourceError, "DNS failure"):
                cli.install(
                    self.agents_root, self.skill_root,
                    ("code-review", "handoff"),
                )

        self.assertEqual(manifest_path.read_bytes(), before_manifest)
        self.assertEqual(
            {path: path.read_bytes() for path in before_files}, before_files
        )
        self.assertFalse((self.skill_root / "code-review").exists())

    def test_remote_update_records_new_sha_and_refuses_to_replace_modified_file(self) -> None:
        first = self.remote_resolver("a" * 40)
        with unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=first):
            self.assertEqual(cli.install(self.agents_root, self.skill_root, ("handoff",)), 0)
        skill = self.skill_root / "handoff" / "SKILL.md"
        manifest_path = self.agents_root / cli.MANIFEST_NAME

        second = self.remote_resolver("b" * 40)
        with unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=second):
            self.assertEqual(cli.install(self.agents_root, self.skill_root, ("handoff",)), 0)
        self.assertIn("b" * 40, skill.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["file_provenance"][skill.as_posix()]["sha"], "b" * 40)

        skill.write_text("locally modified", encoding="utf-8")
        before_manifest = manifest_path.read_bytes()
        third = self.remote_resolver("c" * 40)
        with unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=third):
            self.assertEqual(cli.install(self.agents_root, self.skill_root, ("handoff",)), 2)
        self.assertEqual(skill.read_text(encoding="utf-8"), "locally modified")
        self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_planned_shape_conflict_happens_before_obsolete_removal_or_manifest_write(self) -> None:
        self.assertEqual(cli.install(self.agents_root, self.skill_root, ("orchestrated-delivery",)), 0)
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        before_manifest = manifest_path.read_bytes()
        before_files = {
            path: path.read_bytes()
            for path in cli.source_files(
                self.agents_root, self.skill_root, ("orchestrated-delivery",)
            )
        }
        conflict = self.skill_root / "handoff"
        conflict.write_text("a file blocks the planned skill directory", encoding="utf-8")
        resolver = self.remote_resolver()
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", return_value=resolver
        ):
            self.assertEqual(
                cli.install(self.agents_root, self.skill_root, ("code-review", "handoff")), 2
            )

        self.assertEqual(manifest_path.read_bytes(), before_manifest)
        self.assertEqual(
            {path: path.read_bytes() for path in before_files}, before_files
        )
        self.assertEqual(conflict.read_text(encoding="utf-8"), "a file blocks the planned skill directory")

    def test_chooser_discloses_remote_origin_without_per_skill_source_lines(self) -> None:
        registry = cli.default_registry()
        prompt = unittest.mock.Mock()
        prompt.application.layout.find_all_controls.return_value = ()
        prompt.ask.return_value = ["grill-me"]
        class TTYOutput(io.StringIO):
            def isatty(self) -> bool:
                return True

        output = TTYOutput()
        with (
            unittest.mock.patch.object(cli.sys, "stdin", unittest.mock.Mock(isatty=lambda: True)),
            unittest.mock.patch.object(cli.questionary, "checkbox", return_value=prompt) as checkbox,
            redirect_stdout(output),
        ):
            self.assertEqual(cli.select_components(registry, set()), ("grill-me",))

        disclosure = output.getvalue()
        self.assertIn("First-party:", disclosure)
        self.assertIn("Third-party:", disclosure)
        self.assertIn("Matt Pocock skills (mattpocock/skills)", disclosure)
        self.assertIn("https://github.com/mattpocock/skills | Licence: MIT", disclosure)
        self.assertIn("grilling companion", disclosure)
        self.assertNotIn("Source:", disclosure)
        self.assertNotIn("skills/productivity/", disclosure)
        self.assertNotIn("tracks main; future content from this publisher", disclosure)
        self.assertEqual(checkbox.call_count, 1)

    def test_v1_to_v4_migration_marks_retained_modified_legacy_file_exactly(self) -> None:
        legacy = self.agents_root / "agents" / "legacy.toml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("recorded", encoding="utf-8")
        recorded = hashlib.sha256(legacy.read_bytes()).hexdigest()
        (self.agents_root / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 1,
                    "files": {"agents/legacy.toml": recorded},
                }
            ), encoding="utf-8"
        )
        legacy.write_text("locally modified", encoding="utf-8")

        self.assertEqual(cli.install(self.agents_root, self.skill_root, ("code-review",)), 0)

        manifest = json.loads((self.agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 4)
        self.assertEqual(manifest["file_provenance"][legacy.as_posix()], {"kind": "legacy"})
        self.assertEqual(set(manifest["file_provenance"]), set(manifest["files"]))

    def test_v2_and_v3_migrations_remain_usable_and_mark_retained_legacy_files(self) -> None:
        for version in (2, 3):
            with self.subTest(version=version):
                with tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    agents_root = base / "codex-home"
                    skill_root = base / "home" / ".agents" / "skills"
                    agents_root.mkdir(parents=True)
                    skill_root.mkdir(parents=True)
                    legacy = agents_root / "agents" / "legacy.toml"
                    legacy.parent.mkdir()
                    legacy.write_text("recorded", encoding="utf-8")
                    files = {legacy.as_posix(): hashlib.sha256(legacy.read_bytes()).hexdigest()}
                    manifest: dict[str, object] = {
                        "installer": "codex-orchestrator", "version": version, "files": files,
                    }
                    if version == 3:
                        manifest["components"] = ["orchestrated-delivery"]
                        manifest["file_owners"] = {legacy.as_posix(): ["orchestrated-delivery"]}
                    (agents_root / cli.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
                    legacy.write_text("locally modified", encoding="utf-8")

                    self.assertEqual(cli.install(agents_root, skill_root, ("code-review",)), 0)
                    migrated = json.loads((agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8"))
                    self.assertEqual(migrated["version"], 4)
                    self.assertEqual(migrated["file_provenance"][legacy.as_posix()], {"kind": "legacy"})

    def test_v4_rejects_incomplete_provenance_before_network_free_uninstall(self) -> None:
        owned = self.agents_root / "agents" / "owned.toml"
        owned.parent.mkdir(parents=True)
        owned.write_text("owned", encoding="utf-8")
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator", "version": 4,
                    "files": {owned.as_posix(): hashlib.sha256(owned.read_bytes()).hexdigest()},
                    "file_owners": {owned.as_posix(): ["handoff"]},
                    "file_provenance": {},
                }
            ), encoding="utf-8"
        )

        with self.assertRaisesRegex(RuntimeError, "file_provenance must match files exactly"):
            cli.uninstall(self.agents_root, self.skill_root)
        self.assertTrue(owned.exists())
        self.assertTrue(manifest_path.exists())

    def test_deselection_rejects_tracked_path_beneath_within_root_symlink(self) -> None:
        """A lexical path under a root must not unlink through an internal symlink."""
        target_directory = self.skill_root / "actual-skill"
        target_directory.mkdir()
        target = target_directory / "SKILL.md"
        target.write_text("do not unlink through a link", encoding="utf-8")
        link = self.skill_root / "linked-skill"
        link.symlink_to(target_directory, target_is_directory=True)
        recorded_path = link / "SKILL.md"
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 3,
                    "components": ["orchestrated-delivery"],
                    "files": {
                        recorded_path.as_posix(): hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                    "file_owners": {recorded_path.as_posix(): ["orchestrated-delivery"]},
                }
            ), encoding="utf-8"
        )
        before_manifest = manifest_path.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "incompatible ancestors"):
            cli.install(self.agents_root, self.skill_root, ("code-review",))

        self.assertEqual(target.read_text(encoding="utf-8"), "do not unlink through a link")
        self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_uninstall_rejects_tracked_path_beneath_within_root_symlink(self) -> None:
        target_directory = self.skill_root / "actual-skill"
        target_directory.mkdir()
        target = target_directory / "SKILL.md"
        target.write_text("do not unlink through a link", encoding="utf-8")
        link = self.skill_root / "linked-skill"
        link.symlink_to(target_directory, target_is_directory=True)
        recorded_path = link / "SKILL.md"
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 4,
                    "files": {
                        recorded_path.as_posix(): hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                    "file_owners": {recorded_path.as_posix(): ["handoff"]},
                    "file_provenance": {recorded_path.as_posix(): {"kind": "legacy"}},
                }
            ), encoding="utf-8"
        )
        before_manifest = manifest_path.read_bytes()

        with self.assertRaisesRegex(RuntimeError, "incompatible ancestors"):
            cli.uninstall(self.agents_root, self.skill_root)

        self.assertEqual(target.read_text(encoding="utf-8"), "do not unlink through a link")
        self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_v4_rejects_malformed_github_provenance_metadata_and_paths(self) -> None:
        owned = self.agents_root / "agents" / "owned.toml"
        owned.parent.mkdir(parents=True)
        owned.write_text("owned", encoding="utf-8")
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        files = {owned.as_posix(): hashlib.sha256(owned.read_bytes()).hexdigest()}
        valid_provenance = {
            "kind": "github",
            "path": "skills/productivity/handoff/SKILL.md",
            "owner": "mattpocock",
            "repository": "skills",
            "ref": "main",
            "sha": "a" * 40,
        }
        invalid_values = {
            "owner": "invalid/owner",
            "repository": "skills.git",
            "ref": "main..rewritten",
            "path": "skills/../handoff/SKILL.md",
            "path-backslash": "skills\\handoff\\SKILL.md",
            "path-colon": "skills:handoff/SKILL.md",
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                provenance_field = "path" if field.startswith("path-") else field
                provenance = dict(valid_provenance)
                provenance[provenance_field] = value
                manifest_path.write_text(
                    json.dumps(
                        {
                            "installer": "codex-orchestrator", "version": 4,
                            "files": files,
                            "file_owners": {owned.as_posix(): ["handoff"]},
                            "file_provenance": {owned.as_posix(): provenance},
                        }
                    ), encoding="utf-8"
                )
                before_manifest = manifest_path.read_bytes()

                with self.assertRaisesRegex(RuntimeError, "Invalid file provenance entry"):
                    cli.install(self.agents_root, self.skill_root, ("code-review",))
                self.assertEqual(owned.read_text(encoding="utf-8"), "owned")
                self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_v4_github_provenance_allows_underscore_ref_through_install_and_uninstall(self) -> None:
        registry = cli.default_registry()
        repository_id = "mattpocock-skills"
        repositories = dict(registry.repositories or {})
        repositories[repository_id] = replace(repositories[repository_id], ref="_release")
        registry = replace(registry, repositories=repositories)
        resolver = self.remote_resolver()
        with unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=resolver):
            self.assertEqual(
                cli.install(self.agents_root, self.skill_root, ("handoff",), registry), 0
            )

        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["file_provenance"])
        self.assertTrue(
            all(
                entry.get("ref") == "_release"
                for entry in manifest["file_provenance"].values()
            )
        )
        with unittest.mock.patch.object(
            cli, "GitHubSourceResolver", side_effect=AssertionError("network used")
        ):
            self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 0)
        self.assertFalse(manifest_path.exists())

    def test_v4_rejects_requested_invalid_github_ref_forms_before_mutation(self) -> None:
        owned = self.agents_root / "agents" / "owned.toml"
        owned.parent.mkdir(parents=True)
        owned.write_text("owned", encoding="utf-8")
        manifest_path = self.agents_root / cli.MANIFEST_NAME
        files = {owned.as_posix(): hashlib.sha256(owned.read_bytes()).hexdigest()}
        for ref in ("main/-bad", "release/foo."):
            with self.subTest(ref=ref):
                manifest_path.write_text(
                    json.dumps(
                        {
                            "installer": "codex-orchestrator", "version": 4,
                            "files": files,
                            "file_owners": {owned.as_posix(): ["handoff"]},
                            "file_provenance": {
                                owned.as_posix(): {
                                    "kind": "github",
                                    "path": "skills/productivity/handoff/SKILL.md",
                                    "owner": "mattpocock",
                                    "repository": "skills",
                                    "ref": ref,
                                    "sha": "a" * 40,
                                }
                            },
                        }
                    ), encoding="utf-8"
                )
                before_manifest = manifest_path.read_bytes()

                with self.assertRaisesRegex(RuntimeError, "Invalid file provenance entry"):
                    cli.uninstall(self.agents_root, self.skill_root)
                self.assertEqual(owned.read_text(encoding="utf-8"), "owned")
                self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_interactive_remote_preflight_status_is_visible_and_animates_before_report(self) -> None:
        """The selected remote source keeps a visible status through preflight."""
        events: list[tuple[str, str]] = []
        output = _TTYOutput(events)
        event = _ControlledSpinnerEvent()
        thread: _ControlledSpinnerThread | None = None

        def make_thread(*, target: object, daemon: bool) -> _ControlledSpinnerThread:
            nonlocal thread
            self.assertIsNone(thread)
            thread = _ControlledSpinnerThread(target, daemon)
            return thread

        resolver = unittest.mock.Mock()

        def materialize(repository: object, paths: list[Path], root: Path) -> SimpleNamespace:
            self.assertEqual(selector.call_count, 1)
            self.assertIn("\r| Fetching selected third-party sources", output.getvalue())
            self.assertNotIn("\r\033[2K", output.getvalue())
            self.assertIsNotNone(thread)
            thread.advance()
            self.assertIn("\r/ Fetching selected third-party sources", output.getvalue())
            self.assertNotIn("\r\033[2K", output.getvalue())
            return self.materialize_remote(root, paths)

        resolver.materialize.side_effect = materialize
        home = self.skill_root.parent.parent
        selector = unittest.mock.Mock(return_value=("handoff",))
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(cli.sys, "stdout", output),
            unittest.mock.patch.object(cli, "select_components", selector),
            unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=resolver),
            unittest.mock.patch.object(cli.threading, "Event", return_value=event),
            unittest.mock.patch.object(cli.threading, "Thread", side_effect=make_thread),
        ):
            self.assertEqual(cli.main(["--install"]), 0)

        self.assertIsNotNone(thread)
        self.assertTrue(thread.started)
        self.assertTrue(thread.joined)
        self.assertLess(
            output.getvalue().index("\r\033[2K"), output.getvalue().index("Changes:\n")
        )

    def test_interactive_remote_preflight_status_clears_before_source_error(self) -> None:
        """A failed preflight clears the terminal line before the normal CLI error."""
        events: list[tuple[str, str]] = []
        output = _TTYOutput(events)
        error = _RecordingError(events)
        event = _ControlledSpinnerEvent()
        thread: _ControlledSpinnerThread | None = None

        def make_thread(*, target: object, daemon: bool) -> _ControlledSpinnerThread:
            nonlocal thread
            thread = _ControlledSpinnerThread(target, daemon)
            return thread

        resolver = unittest.mock.Mock()

        def fail_preflight(repository: object, paths: list[Path], root: Path) -> SimpleNamespace:
            self.assertIn("\r| Fetching selected third-party sources", output.getvalue())
            self.assertNotIn("\r\033[2K", output.getvalue())
            self.assertIsNotNone(thread)
            thread.advance()
            self.assertIn("\r/ Fetching selected third-party sources", output.getvalue())
            raise cli.SourceError("DNS failure")

        resolver.materialize.side_effect = fail_preflight
        home = self.skill_root.parent.parent
        selector = unittest.mock.Mock(return_value=("handoff",))
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(cli.sys, "stdout", output),
            unittest.mock.patch.object(cli.sys, "stderr", error),
            unittest.mock.patch.object(cli, "select_components", selector),
            unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=resolver),
            unittest.mock.patch.object(cli.threading, "Event", return_value=event),
            unittest.mock.patch.object(cli.threading, "Thread", side_effect=make_thread),
        ):
            self.assertEqual(cli.main(["--install"]), 1)

        self.assertTrue(thread.joined)
        clear = next(index for index, item in enumerate(events) if item == ("stdout", "\r\033[2K"))
        failure = next(
            index for index, (stream, value) in enumerate(events)
            if stream == "stderr" and "error: mattpocock/skills@main: DNS failure" in value
        )
        self.assertLess(clear, failure)

    def test_interactive_bundled_selection_never_starts_remote_status(self) -> None:
        output = _TTYOutput()
        home = self.skill_root.parent.parent
        selector = unittest.mock.Mock(return_value=("code-review",))
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(cli.sys, "stdout", output),
            unittest.mock.patch.object(cli, "select_components", selector),
            unittest.mock.patch.object(
                cli.threading, "Thread", side_effect=AssertionError("spinner started")
            ) as spinner_thread,
        ):
            self.assertEqual(cli.main(["--install"]), 0)

        self.assertEqual(selector.call_count, 1)
        spinner_thread.assert_not_called()
        self.assertNotIn("Fetching selected third-party sources", output.getvalue())

    def test_noninteractive_component_and_all_installs_never_show_remote_status(self) -> None:
        output = _TTYOutput()
        home = self.skill_root.parent.parent
        resolver = self.remote_resolver()
        with (
            unittest.mock.patch.dict(
                "os.environ", {"HOME": str(home), "CODEX_HOME": str(self.agents_root)}, clear=False
            ),
            unittest.mock.patch.object(cli.sys, "stdout", output),
            unittest.mock.patch.object(cli, "GitHubSourceResolver", return_value=resolver),
            unittest.mock.patch.object(
                cli.threading, "Thread", side_effect=AssertionError("spinner started")
            ) as spinner_thread,
        ):
            self.assertEqual(cli.main(["--install", "--components", "handoff"]), 0)
            self.assertEqual(cli.main(["--install", "--all", "--include-third-party"]), 0)

        spinner_thread.assert_not_called()
        self.assertNotIn("Fetching selected third-party sources", output.getvalue())


if __name__ == "__main__":
    unittest.main()
