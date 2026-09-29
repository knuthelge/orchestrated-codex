"""Tests for rendering target resources and exporting the consumer packages."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import support
from orchestrated.registry import load_registry
from orchestrated.targets import TARGETS

export = support.export
REPO_ROOT = support.REPO_ROOT
ENGINE_MODULES = ("cli.py", "registry.py", "runtime.py", "sources.py")

# Each target's resources must not leak the other harness's vocabulary. Invocation tokens
# match only where they start an invocation, not inside paths such as
# skills/orchestrated-delivery.
FORBIDDEN = {
    "codex": (
        r"AskUserQuestion", r"subagent_type", r"CLAUDE_CONFIG_DIR", r"disallowedTools",
        r"(?<![\w/.-])/orchestrated-", r"\bopus\b", r"\bsonnet\b", r"\bhaiku\b",
        r"\bworker\b", r"askQuestions", r"(?<![\w/.-])\$code-review",
    ),
    "claude": (
        r"wait_agent", r"fork_turns", r"gpt-", r"CODEX_HOME", r"(?<![\w/.-])\$orchestrated-",
        r"(?<![\w/.-])\$code-review", r"\bworker\b", r"askQuestions", r"openai\.yaml",
        r"\bCodex\b",
    ),
}


class RenderTests(unittest.TestCase):
    def test_rendering_is_deterministic(self) -> None:
        for name in TARGETS:
            with self.subTest(target=name):
                self.assertEqual(
                    export.render_target(REPO_ROOT, name), export.render_target(REPO_ROOT, name)
                )

    def test_rendered_resources_do_not_leak_other_harness_vocabulary(self) -> None:
        for name, patterns in FORBIDDEN.items():
            for path, text in sorted(export.render_target(REPO_ROOT, name).items()):
                for pattern in patterns:
                    with self.subTest(target=name, file=path, pattern=pattern):
                        self.assertIsNone(re.search(pattern, text))

    def test_rendered_registries_load(self) -> None:
        for name in TARGETS:
            with self.subTest(target=name):
                root = support.resource_root(name)
                registry = load_registry(root / "install-components.yaml", root)
                self.assertEqual(
                    [component.id for component in registry.components],
                    ["orchestrated-delivery", "code-review", "grill-me", "handoff", "teach"],
                )

    def test_engine_modules_name_no_harness(self) -> None:
        # Harness-specific values belong in targets.py; entry points activate a target.
        for name in ENGINE_MODULES:
            text = (REPO_ROOT / "src" / "orchestrated" / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertIsNone(re.search(r"(?i)codex|claude", text))

    def test_every_target_has_a_vocabulary_and_package_metadata(self) -> None:
        vocabularies = export.load_targets(REPO_ROOT / "content")
        self.assertEqual(set(vocabularies), set(TARGETS))
        for name, vocabulary in vocabularies.items():
            with self.subTest(target=name):
                self.assertEqual(
                    set(vocabulary["package"]), {"module", "description", "keywords"}
                )


class SourceEditTests(unittest.TestCase):
    """Render from a disposable copy of content/ to check strictness and overrides."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        shutil.copytree(REPO_ROOT / "content", self.root / "content")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def edit(self, relative: str, old: str, new: str) -> None:
        path = self.root / "content" / relative
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_unknown_placeholder_fails_the_render(self) -> None:
        self.edit("agents/tester.md", "gate", "{{ nope }}")

        for name in TARGETS:
            with self.subTest(target=name), self.assertRaisesRegex(
                export.RenderError, "agents/tester.md"
            ):
                export.render_target(self.root, name)

    def test_invalid_tier_fails_the_render(self) -> None:
        self.edit("agents/tester.md", "tier: standard", "tier: huge")

        with self.assertRaisesRegex(export.RenderError, "tier must be one of"):
            export.render_target(self.root, "codex")

    def test_model_override_applies_to_its_target_only(self) -> None:
        self.edit("agents/tester.md", "tier: standard\n", "tier: standard\nmodel:\n  codex: gpt-test\n")

        codex = export.render_target(self.root, "codex")["agents/tester.toml"]
        claude = export.render_target(self.root, "claude")["agents/tester.md"]

        self.assertIn('model = "gpt-test"\n', codex)
        self.assertIn("model: sonnet\n", claude)

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(export.RenderError, "unknown target"):
            export.render_target(self.root, "copilot")


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temporary_directory.name)
        cls.exported = {
            path.name: path for path in export.export(REPO_ROOT, cls.out)
        }
        with (REPO_ROOT / "pyproject.toml").open("rb") as file:
            cls.root_project = tomllib.load(file)["project"]
        cls.vocabularies = export.load_targets(REPO_ROOT / "content")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def package(self, name: str) -> tuple[Path, str]:
        target = TARGETS[name]
        return self.exported[target.distribution], str(self.vocabularies[name]["package"]["module"])

    def test_exports_one_package_per_target(self) -> None:
        self.assertEqual(
            set(self.exported), {target.distribution for target in TARGETS.values()}
        )

    def test_package_metadata_comes_from_the_root_project(self) -> None:
        for name, target in TARGETS.items():
            with self.subTest(target=name):
                root, module = self.package(name)
                with (root / "pyproject.toml").open("rb") as file:
                    project = tomllib.load(file)
                metadata = project["project"]
                self.assertEqual(metadata["name"], target.distribution)
                self.assertEqual(metadata["version"], self.root_project["version"])
                self.assertEqual(metadata["dependencies"], self.root_project["dependencies"])
                self.assertEqual(metadata["requires-python"], self.root_project["requires-python"])
                self.assertFalse(any(c.startswith("Private ::") for c in metadata["classifiers"]))
                self.assertEqual(metadata["scripts"], {target.distribution: f"{module}.entry:main"})
                self.assertEqual(project["tool"]["uv"]["build-backend"]["module-name"], module)

    def test_package_contains_readme_engine_entry_and_rendered_resources(self) -> None:
        for name in TARGETS:
            with self.subTest(target=name):
                root, module = self.package(name)
                source = root / "src" / module
                self.assertEqual(
                    (root / "README.md").read_bytes(), (REPO_ROOT / "README.md").read_bytes()
                )
                for engine_file in (*ENGINE_MODULES, "targets.py"):
                    self.assertEqual(
                        (source / engine_file).read_bytes(),
                        (REPO_ROOT / "src" / "orchestrated" / engine_file).read_bytes(),
                    )
                self.assertIn(f'TARGETS["{name}"]', (source / "entry.py").read_text(encoding="utf-8"))
                self.assertIn(
                    f'__version__ = "{self.root_project["version"]}"',
                    (source / "__init__.py").read_text(encoding="utf-8"),
                )
                rendered = export.render_target(REPO_ROOT, name)
                bundled = {
                    path.relative_to(source / "resources").as_posix()
                    for path in (source / "resources").rglob("*")
                    if path.is_file()
                }
                self.assertEqual(bundled, set(rendered))

    def test_exported_entry_point_runs_its_own_target(self) -> None:
        for name, target in TARGETS.items():
            with self.subTest(target=name), tempfile.TemporaryDirectory() as home:
                root, module = self.package(name)
                env = {key: value for key, value in os.environ.items() if key != target.home_env}
                env.update(HOME=home, PYTHONPATH=str(root / "src"))
                env[target.home_env] = str(Path(home) / "config")
                result = subprocess.run(
                    [sys.executable, "-m", module, "--uninstall"],
                    capture_output=True, text=True, env=env, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(target.manifest_name, result.stdout)

    def test_export_replaces_an_earlier_export(self) -> None:
        stale = self.out / "orchestrated-claude" / "stale.txt"
        stale.write_text("stale", encoding="utf-8")

        export.export(REPO_ROOT, self.out, ["claude"])

        self.assertFalse(stale.exists())
        self.assertTrue((self.out / "orchestrated-claude" / "pyproject.toml").is_file())


if __name__ == "__main__":
    unittest.main()
