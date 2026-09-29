"""Harness-specific installer behavior, run against both distributions.

The installer engine is identical in both packages (see test_render), so the detailed
engine behavior in test_cli runs once. These tests cover what each package's target
changes: default roots, the home flag, manifest identity, and report labels.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

import claude_orchestrator.cli
import codex_orchestrator.cli
from codex_orchestrator.registry import load_registry


class TargetBehaviorMixin:
    cli = codex_orchestrator.cli
    agent_ext = ".toml"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        base = Path(self.temporary_directory.name)
        self.home = base / "home"
        self.config_home = base / "config"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @property
    def target(self):
        return self.cli.TARGET

    def environment(self, **overrides: str) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key != self.target.home_env}
        env["HOME"] = str(self.home)
        env.update(overrides)
        return env

    def expected_skill_root(self, config_home: Path) -> Path:
        if self.target.skills_under_config_home:
            return config_home / "skills"
        return self.home / ".agents" / "skills"

    def run_main(self, argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        output = io.StringIO()
        with unittest.mock.patch.dict("os.environ", env, clear=True), redirect_stdout(output):
            status = self.cli.main(argv)
        return status, output.getvalue()

    def test_default_roots_follow_the_home_variable(self) -> None:
        with unittest.mock.patch.dict("os.environ", self.environment(), clear=True):
            default_home = self.home / self.target.home_default
            self.assertEqual(self.cli.resolve_agents_root(), default_home)
            self.assertEqual(self.cli.resolve_skill_root(), self.expected_skill_root(default_home))
        env = self.environment(**{self.target.home_env: str(self.config_home)})
        with unittest.mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(self.cli.resolve_agents_root(), self.config_home)
            self.assertEqual(
                self.cli.resolve_skill_root(), self.expected_skill_root(self.config_home)
            )

    def test_install_and_uninstall_through_the_environment(self) -> None:
        env = self.environment(**{self.target.home_env: str(self.config_home)})
        skill_root = self.expected_skill_root(self.config_home)

        status, output = self.run_main(["--install", "--all"], env)

        self.assertEqual(status, 0, output)
        self.assertTrue((skill_root / "orchestrated-delivery" / "SKILL.md").is_file())
        self.assertTrue((skill_root / "orchestrated-code-review" / "SKILL.md").is_file())
        self.assertTrue((self.config_home / "agents" / f"developer{self.agent_ext}").is_file())
        manifest = json.loads(
            (self.config_home / self.target.manifest_name).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["installer"], self.target.installer_id)
        self.assertIn(
            f"{self.target.skills_label}/orchestrated-code-review/SKILL.md", output
        )
        self.assertIn(f"Restart {self.target.product} or start a new conversation.", output)

        status, output = self.run_main(["--uninstall"], env)

        self.assertEqual(status, 0, output)
        self.assertFalse((skill_root / "orchestrated-delivery").exists())
        self.assertFalse((self.config_home / self.target.manifest_name).exists())

    def test_home_flag_overrides_the_environment(self) -> None:
        override = Path(self.temporary_directory.name) / "override"
        env = self.environment(**{self.target.home_env: str(self.config_home)})

        status, output = self.run_main(
            ["--install", "--components", "code-review", self.target.home_flag, str(override)],
            env,
        )

        self.assertEqual(status, 0, output)
        self.assertTrue((override / self.target.manifest_name).is_file())
        self.assertFalse(self.config_home.exists())
        self.assertTrue(
            (self.expected_skill_root(override) / "orchestrated-code-review" / "SKILL.md").is_file()
        )

    def test_manifest_from_another_installer_is_refused(self) -> None:
        self.config_home.mkdir()
        (self.config_home / self.target.manifest_name).write_text(
            json.dumps({"installer": "some-other-installer", "version": 4, "files": {}}),
            encoding="utf-8",
        )
        env = self.environment(**{self.target.home_env: str(self.config_home)})

        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            status, _ = self.run_main(["--uninstall"], env)

        self.assertEqual(status, 1)


class CodexTargetTests(TargetBehaviorMixin, unittest.TestCase):
    cli = codex_orchestrator.cli
    agent_ext = ".toml"


class ClaudeTargetTests(TargetBehaviorMixin, unittest.TestCase):
    cli = claude_orchestrator.cli
    agent_ext = ".md"


class NestedRootTests(unittest.TestCase):
    """Claude's skills root sits inside its config home; resources must not overlap there."""

    def test_file_inside_another_resources_bundled_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bundle").mkdir()
            (root / "bundle" / "SKILL.md").write_text("skill", encoding="utf-8")
            (root / "extra.md").write_text("extra", encoding="utf-8")
            registry_path = root / "registry.yaml"
            registry_path.write_text(
                """\
schema_version: 1
components:
  - id: nested
    title: Nested
    active: true
    description: Overlapping resources.
    includes: Two resources.
    resources:
      - source: bundle
        install_root: skills
        destination: x
      - source: extra.md
        install_root: config_home
        destination: skills/x/extra.md
""",
                encoding="utf-8",
            )
            registry = load_registry(registry_path, root)
            config_home = root / "config"

            with self.assertRaisesRegex(RuntimeError, "lies inside bundled directory"):
                codex_orchestrator.cli.source_plan(
                    config_home, config_home / "skills", ("nested",), registry
                )


if __name__ == "__main__":
    unittest.main()
