"""Tests for the source-checkout launcher's --codex and --claude selection."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("source_launcher", REPO_ROOT / "main.py")
assert _SPEC is not None and _SPEC.loader is not None
launcher = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = launcher
_SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        home = Path(self.temporary_directory.name)
        env = {
            key: value for key, value in os.environ.items()
            if key not in ("CODEX_HOME", "CLAUDE_CONFIG_DIR")
        }
        env["HOME"] = str(home)
        patcher = unittest.mock.patch.dict("os.environ", env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    def uninstall_output(self, *flags: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(launcher.main([*flags, "--uninstall"]), 0)
        return output.getvalue()

    def test_defaults_to_codex(self) -> None:
        self.assertIn("codex-orchestrator-install.json", self.uninstall_output())

    def test_codex_flag_selects_codex(self) -> None:
        self.assertIn("codex-orchestrator-install.json", self.uninstall_output("--codex"))

    def test_claude_flag_selects_claude(self) -> None:
        self.assertIn("claude-orchestrator-install.json", self.uninstall_output("--claude"))

    def test_flag_position_does_not_matter(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(launcher.main(["--uninstall", "--claude"]), 0)
        self.assertIn("claude-orchestrator-install.json", output.getvalue())

    def test_both_flags_are_rejected(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(launcher.main(["--codex", "--claude", "--uninstall"]), 2)
        self.assertIn("only one of --codex and --claude", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
