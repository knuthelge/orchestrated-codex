"""Tests for rendering harness-specific resources from content/."""

from __future__ import annotations

import importlib.util
import re
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
CLAUDE_PACKAGE = Path("packages/orchestrated-claude/src/claude_orchestrator")
CLAUDE_OUTPUT = CLAUDE_PACKAGE / "resources"
ENGINE_MODULES = ("cli.py", "registry.py", "sources.py", "__main__.py")

# Each target's generated resources must not leak the other harness's vocabulary. Invocation
# tokens match only where they start an invocation, not inside paths such as
# skills/orchestrated-delivery.
FORBIDDEN = {
    CODEX_OUTPUT: (
        r"AskUserQuestion", r"subagent_type", r"CLAUDE_CONFIG_DIR", r"disallowedTools",
        r"(?<![\w/.-])/orchestrated-", r"\bopus\b", r"\bsonnet\b", r"\bhaiku\b",
        r"\bworker\b", r"askQuestions", r"(?<![\w/.-])\$code-review",
    ),
    CLAUDE_OUTPUT: (
        r"wait_agent", r"fork_turns", r"gpt-", r"CODEX_HOME", r"(?<![\w/.-])\$orchestrated-",
        r"(?<![\w/.-])\$code-review", r"\bworker\b", r"askQuestions", r"openai\.yaml",
        r"\bCodex\b",
    ),
}


class CommittedResourcesTests(unittest.TestCase):
    def test_committed_resources_match_a_fresh_render(self) -> None:
        self.assertEqual(render_resources.check(REPO_ROOT), [])

    def test_generated_resources_do_not_leak_other_harness_vocabulary(self) -> None:
        for output, patterns in FORBIDDEN.items():
            for path in sorted((REPO_ROOT / output).rglob("*")):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    with self.subTest(file=str(path.relative_to(REPO_ROOT)), pattern=pattern):
                        self.assertIsNone(re.search(pattern, text))

    def test_both_rendered_registries_load(self) -> None:
        from codex_orchestrator.registry import load_registry

        for output in (CODEX_OUTPUT, CLAUDE_OUTPUT):
            with self.subTest(output=str(output)):
                registry = load_registry(
                    REPO_ROOT / output / "install-components.yaml", REPO_ROOT / output
                )
                self.assertEqual(
                    [component.id for component in registry.components],
                    ["orchestrated-delivery", "code-review", "grill-me", "handoff", "teach"],
                )

    def test_claude_engine_is_identical_to_the_canonical_engine(self) -> None:
        for name in (*ENGINE_MODULES, "target.py"):
            with self.subTest(module=name):
                self.assertEqual(
                    (REPO_ROOT / CLAUDE_PACKAGE / name).read_bytes(),
                    (REPO_ROOT / "src" / "codex_orchestrator" / name).read_bytes(),
                )

    def test_engine_modules_name_no_harness(self) -> None:
        # Harness-specific values belong in target.py and each package's _active.py.
        for name in ENGINE_MODULES:
            text = (REPO_ROOT / "src" / "codex_orchestrator" / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertIsNone(re.search(r"(?i)codex|claude", text))

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
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(REPO_ROOT / "content", self.root / "content")
        shutil.copytree(
            REPO_ROOT / "src" / "codex_orchestrator", self.root / "src" / "codex_orchestrator",
            ignore=ignore,
        )
        shutil.copytree(REPO_ROOT / CLAUDE_PACKAGE, self.root / CLAUDE_PACKAGE, ignore=ignore)
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
            [
                f"stale: {CODEX_OUTPUT.as_posix()}/agents/tester.toml",
                f"stale: {CLAUDE_OUTPUT.as_posix()}/agents/tester.md",
            ],
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

    def test_edited_engine_mirror_is_detected_and_restored(self) -> None:
        mirror = self.root / CLAUDE_PACKAGE / "cli.py"
        mirror.write_text(mirror.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")

        self.assertEqual(
            render_resources.check(self.root), [f"stale: {CLAUDE_PACKAGE.as_posix()}/cli.py"]
        )
        render_resources.write(self.root)
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
