"""Installer tests for the two-root layout (agents under codex-home, skill under $HOME)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import unittest.mock
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

    def test_install_places_skill_and_agents_in_independent_roots(self) -> None:
        expected = self.files()

        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        manifest_path = self.agents_root / cli.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["files"]), {path.as_posix() for path in expected})
        for destination, source in expected.items():
            self.assertEqual(destination.read_bytes(), source.read_bytes())

        # SC-1: skill lands under $HOME/.agents/skills/orchestrated-delivery.
        skill_dir = self.skill_root / "orchestrated-delivery"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())
        self.assertTrue((skill_dir / "references").is_dir())

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

    def test_uninstall_removes_files_from_both_roots(self) -> None:
        expected = self.files()
        self.assertEqual(cli.install(self.agents_root, self.skill_root), 0)

        self.assertEqual(cli.uninstall(self.agents_root, self.skill_root), 0)

        self.assertFalse((self.agents_root / cli.MANIFEST_NAME).exists())
        for destination in expected:
            self.assertFalse(destination.exists())
        self.assertFalse((self.skill_root / "orchestrated-delivery").exists())

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
        legacy_skill = self.agents_root / "skills" / "orchestrated-delivery" / "SKILL.md"
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
        legacy_skill = self.agents_root / "skills" / "orchestrated-delivery" / "SKILL.md"
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
            self.assertEqual(cli.main(["--install"]), 0)

        self.assertTrue(
            (home / ".agents" / "skills" / "orchestrated-delivery" / "SKILL.md").is_file()
        )
        self.assertTrue((codex_home / "agents" / "tester.toml").is_file())


if __name__ == "__main__":
    unittest.main()
