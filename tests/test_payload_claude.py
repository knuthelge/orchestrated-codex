"""Payload tests for the Claude Code resources: agent and skill frontmatter and content."""

from __future__ import annotations

import re
import unittest

import yaml

from claude_orchestrator import cli

AGENTS_DIR = cli.RESOURCE_ROOT / "agents"
SKILLS_DIR = cli.RESOURCE_ROOT / "skills"

# A project policy: agents pin a model alias; Claude Code also accepts fable and full IDs.
EXPECTED_MODELS = {
    "developer": "sonnet",
    "discovery": "haiku",
    "final-reviewer": "opus",
    "rubber-duck": "opus",
    "spec-designer": "opus",
    "tester": "sonnet",
    "ui-designer": "opus",
}
EXPECTED_EFFORT = {name: "high" for name in EXPECTED_MODELS} | {"discovery": "xhigh"}
READ_ONLY_AGENTS = {"discovery", "final-reviewer", "rubber-duck"}


def split(text: str) -> tuple[dict[str, object], str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        raise AssertionError("missing YAML frontmatter")
    return yaml.safe_load(match.group(1)), text[match.end():]


class ClaudeAgentPayloadTests(unittest.TestCase):
    def agents(self) -> dict[str, tuple[dict[str, object], str]]:
        return {
            path.stem: split(path.read_text(encoding="utf-8"))
            for path in sorted(AGENTS_DIR.glob("*.md"))
        }

    def test_expected_agents_present(self) -> None:
        self.assertEqual(set(self.agents()), set(EXPECTED_MODELS))

    def test_frontmatter_schema(self) -> None:
        for stem, (meta, body) in self.agents().items():
            with self.subTest(agent=stem):
                self.assertEqual(
                    set(meta), {"name", "description", "model", "effort", "disallowedTools"}
                )
                self.assertEqual(meta["name"], stem)
                self.assertRegex(stem, r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
                self.assertTrue(str(meta["description"]).strip())
                self.assertEqual(meta["model"], EXPECTED_MODELS[stem])
                self.assertEqual(meta["effort"], EXPECTED_EFFORT[stem])
                self.assertTrue(body.strip())

    def test_no_agent_spawns_subagents_and_read_only_agents_cannot_edit(self) -> None:
        for stem, (meta, _) in self.agents().items():
            with self.subTest(agent=stem):
                expected = "Agent, Edit, Write, NotebookEdit" if stem in READ_ONLY_AGENTS else "Agent"
                self.assertEqual(meta["disallowedTools"], expected)


class ClaudeSkillPayloadTests(unittest.TestCase):
    def test_skills_are_explicit_only_and_named_after_their_directory(self) -> None:
        names = sorted(path.name for path in SKILLS_DIR.iterdir() if path.is_dir())
        self.assertEqual(names, ["orchestrated-code-review", "orchestrated-delivery"])
        for name in names:
            with self.subTest(skill=name):
                meta, _ = split((SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8"))
                self.assertEqual(meta["name"], name)
                self.assertIs(meta["disable-model-invocation"], True)
                self.assertFalse((SKILLS_DIR / name / "agents").exists())

    def test_delivery_skill_uses_claude_delegation_and_names(self) -> None:
        _, body = split(
            (SKILLS_DIR / "orchestrated-delivery" / "SKILL.md").read_text(encoding="utf-8")
        )
        normalized = " ".join(body.split())
        self.assertIn("Subagents start with fresh context", normalized)
        self.assertIn("never poll, sleep, or check status in a loop", normalized)
        self.assertIn("the AskUserQuestion tool", normalized)
        self.assertIn("Implementation is delegated to `developer`", normalized)
        self.assertIn("`rubber-duck`", body)
        self.assertIn("(`final-reviewer`, `rubber-duck`, `spec-designer`, and `ui-designer`)", normalized)

    def test_code_review_skill_references_its_own_name(self) -> None:
        meta, body = split(
            (SKILLS_DIR / "orchestrated-code-review" / "SKILL.md").read_text(encoding="utf-8")
        )
        self.assertIn("/orchestrated-code-review", str(meta["description"]))
        self.assertIn("current Claude Code environment", " ".join(body.split()))


if __name__ == "__main__":
    unittest.main()
