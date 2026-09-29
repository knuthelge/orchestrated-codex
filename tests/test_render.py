"""Tests for rendering harness-specific resources from content/."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "render_resources", REPO_ROOT / "scripts" / "render_resources.py"
)
assert _SPEC is not None and _SPEC.loader is not None
render_resources = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = render_resources
_SPEC.loader.exec_module(render_resources)

CODEX_OUTPUT = Path("src/codex_orchestrator/resources")


class CommittedResourcesTests(unittest.TestCase):
    def test_committed_resources_match_a_fresh_render(self) -> None:
        self.assertEqual(render_resources.check(REPO_ROOT), [])

    def test_rendering_is_deterministic(self) -> None:
        self.assertEqual(
            render_resources.render_targets(REPO_ROOT),
            render_resources.render_targets(REPO_ROOT),
        )


class RenderCheckTests(unittest.TestCase):
    """Exercise check and write against a disposable copy of the sources and output."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        shutil.copytree(REPO_ROOT / "content", self.root / "content")
        shutil.copytree(REPO_ROOT / CODEX_OUTPUT, self.root / CODEX_OUTPUT)
        self.output = self.root / CODEX_OUTPUT

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def test_copy_starts_clean(self) -> None:
        self.assertEqual(render_resources.check(self.root), [])

    def test_source_edit_makes_output_stale(self) -> None:
        self.edit(self.root / "content" / "agents" / "tester.md", "gate", "gates")

        self.assertEqual(
            render_resources.check(self.root),
            [f"stale: {CODEX_OUTPUT.as_posix()}/agents/tester.toml"],
        )

    def test_generated_file_edit_is_detected(self) -> None:
        self.edit(self.output / "skills" / "orchestrated-code-review" / "SKILL.md", "Review", "Reveiw")

        self.assertEqual(
            render_resources.check(self.root),
            [f"stale: {CODEX_OUTPUT.as_posix()}/skills/orchestrated-code-review/SKILL.md"],
        )

    def test_missing_and_unexpected_files_are_detected(self) -> None:
        (self.output / "agents" / "tester.toml").unlink()
        (self.output / "agents" / "extra.toml").write_text("", encoding="utf-8")

        self.assertEqual(
            render_resources.check(self.root),
            [
                f"unexpected: {CODEX_OUTPUT.as_posix()}/agents/extra.toml",
                f"missing: {CODEX_OUTPUT.as_posix()}/agents/tester.toml",
            ],
        )

    def test_write_restores_output_and_removes_stale_files(self) -> None:
        (self.output / "agents" / "tester.toml").write_text("stale", encoding="utf-8")
        stray = self.output / "skills" / "retired" / "SKILL.md"
        stray.parent.mkdir(parents=True)
        stray.write_text("", encoding="utf-8")

        changed = render_resources.write(self.root)

        self.assertIn(f"wrote: {CODEX_OUTPUT.as_posix()}/agents/tester.toml", changed)
        self.assertIn(f"removed: {CODEX_OUTPUT.as_posix()}/skills/retired/SKILL.md", changed)
        self.assertFalse(stray.parent.exists())
        self.assertEqual(render_resources.check(self.root), [])

    def test_unknown_placeholder_fails_the_render(self) -> None:
        self.edit(self.root / "content" / "agents" / "tester.md", "gate", "{{ nope }}")

        with self.assertRaisesRegex(render_resources.RenderError, "agents/tester.md"):
            render_resources.check(self.root)

    def test_invalid_tier_fails_the_render(self) -> None:
        self.edit(self.root / "content" / "agents" / "tester.md", "tier: standard", "tier: huge")

        with self.assertRaisesRegex(render_resources.RenderError, "tier must be one of"):
            render_resources.check(self.root)

    def test_model_override_replaces_the_tier_model(self) -> None:
        self.edit(
            self.root / "content" / "agents" / "tester.md",
            "tier: standard\n",
            "tier: standard\nmodel:\n  codex: gpt-test\n",
        )

        files = render_resources.render_targets(self.root)[self.output]

        self.assertIn('model = "gpt-test"\n', files["agents/tester.toml"])


if __name__ == "__main__":
    unittest.main()
