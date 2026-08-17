from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from codex_orchestrator import cli


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.codex_home = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_install_and_uninstall_all_resources(self) -> None:
        expected = cli.source_files()

        self.assertEqual(cli.main(["--install", "--codex-home", str(self.codex_home)]), 0)

        manifest_path = self.codex_home / cli.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["files"]), {path.as_posix() for path in expected})
        for relative, source in expected.items():
            self.assertEqual((self.codex_home / relative).read_bytes(), source.read_bytes())

        self.assertEqual(cli.main(["--uninstall", "--codex-home", str(self.codex_home)]), 0)
        self.assertFalse(manifest_path.exists())
        for relative in expected:
            self.assertFalse((self.codex_home / relative).exists())

    def test_install_refuses_foreign_file(self) -> None:
        relative = next(iter(cli.source_files()))
        destination = self.codex_home / relative
        destination.parent.mkdir(parents=True)
        destination.write_text("user content", encoding="utf-8")

        self.assertEqual(cli.install(self.codex_home), 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "user content")
        self.assertFalse((self.codex_home / cli.MANIFEST_NAME).exists())

    def test_uninstall_preserves_modified_file_and_manifest(self) -> None:
        self.assertEqual(cli.install(self.codex_home), 0)
        relative = next(iter(cli.source_files()))
        destination = self.codex_home / relative
        destination.write_text("locally modified", encoding="utf-8")

        self.assertEqual(cli.uninstall(self.codex_home), 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "locally modified")
        self.assertTrue((self.codex_home / cli.MANIFEST_NAME).exists())

    def test_reinstall_removes_unchanged_obsolete_file(self) -> None:
        obsolete_relative = Path("agents/obsolete.toml")
        obsolete = self.codex_home / obsolete_relative
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("old", encoding="utf-8")
        recorded = hashlib.sha256(obsolete.read_bytes()).hexdigest()
        (self.codex_home / cli.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "installer": "codex-orchestrator",
                    "version": 1,
                    "files": {obsolete_relative.as_posix(): recorded},
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(cli.install(self.codex_home), 0)
        self.assertFalse(obsolete.exists())

    def test_foreign_manifest_is_rejected(self) -> None:
        (self.codex_home / cli.MANIFEST_NAME).write_text(
            json.dumps({"installer": "some-other-tool", "files": {}}),
            encoding="utf-8",
        )

        self.assertEqual(cli.main(["--install", "--codex-home", str(self.codex_home)]), 1)


if __name__ == "__main__":
    unittest.main()
