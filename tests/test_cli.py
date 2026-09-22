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

from codex_orchestrator import cli


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
        self.assertEqual(manifest["version"], 3)
        self.assertEqual(manifest["components"], ["code-review"])
        self.assertTrue(manifest["file_owners"])
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
        self.assertFalse(choices[0].checked)
        self.assertTrue(choices[1].checked)
        rendered_titles = [
            "".join(fragment for _, fragment in choice.title) for choice in choices
        ]
        self.assertEqual(
            rendered_titles,
            [
                "Orchestrated delivery - Skill + 6 custom agents.",
                "Code review - Skill + supporting references.",
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


if __name__ == "__main__":
    unittest.main()
