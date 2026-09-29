"""Payload tests for the resources rendered for each target.

Workflow content (routes, gates, contracts, and the code-review procedure) is shared, so
those tests run for every target. Each target also has tests for its own format: Codex
agent TOML and openai.yaml skill metadata, and Claude Code agent and skill frontmatter.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

import yaml

import support
from orchestrated import cli


EXPECTED_MODELS = {
    "developer": "gpt-5.6-terra",
    "discovery": "gpt-6-luna",
    "final_reviewer": "gpt-6-sol",
    "rubber_duck": "gpt-6-sol",
    "spec_designer": "gpt-6-sol",
    "tester": "gpt-5.6-terra",
    "ui_designer": "gpt-6-sol",
}
READ_ONLY_AGENTS = {"discovery", "final_reviewer", "rubber_duck"}
WRITE_AGENTS = {"spec_designer", "ui_designer", "developer", "tester"}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("SKILL.md is missing YAML frontmatter")
    end = lines.index("---", 1)
    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1 :])
    return frontmatter, body


class CodexAgentPayloadTests(support.TargetMixin, unittest.TestCase):
    target_name = "codex"

    def agent_toml_paths(self) -> list[Path]:
        return sorted((self.resources / "agents").glob("*.toml"))

    def test_expected_agent_files_present(self) -> None:
        names = {path.name for path in self.agent_toml_paths()}
        self.assertEqual(
            names,
            {
                "discovery.toml",
                "spec-designer.toml",
                "rubber-duck.toml",
                "ui-designer.toml",
                "developer.toml",
                "tester.toml",
                "final-reviewer.toml",
            },
        )
        self.assertNotIn("verifier.toml", names)

    def test_agent_model_routing(self) -> None:  # SC-3
        actual = {}
        for path in self.agent_toml_paths():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            actual[data["name"]] = data.get("model")
        self.assertEqual(actual, EXPECTED_MODELS)

    def test_schema_and_sandbox_mode(self) -> None:  # SC-4
        for path in self.agent_toml_paths():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for key in ("name", "description", "developer_instructions"):
                self.assertIn(key, data, f"{path.name} missing {key}")
                self.assertTrue(str(data[key]).strip(), f"{path.name} has empty {key}")
            name = data["name"]
            if "sandbox_mode" in data:
                self.assertEqual(
                    data["sandbox_mode"],
                    "read-only",
                    f"{path.name} may only declare sandbox_mode = 'read-only'",
                )
            if name in WRITE_AGENTS:
                self.assertNotIn(
                    "sandbox_mode", data, f"{path.name} is write-capable and must not pin sandbox_mode"
                )

    def test_read_only_agents_pin_sandbox_mode(self) -> None:  # SC-4
        seen = set()
        for path in self.agent_toml_paths():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if data["name"] in READ_ONLY_AGENTS:
                seen.add(data["name"])
                self.assertEqual(
                    data.get("sandbox_mode"),
                    "read-only",
                    f"{path.name} must pin sandbox_mode = 'read-only'",
                )
        self.assertEqual(seen, READ_ONLY_AGENTS)

    def test_developer_keeps_report_contract_and_prohibitions(self) -> None:
        data = tomllib.loads((self.resources / "agents" / "developer.toml").read_text(encoding="utf-8"))
        instructions = data["developer_instructions"]
        self.assertEqual(data["name"], "developer")
        self.assertEqual(data["model_reasoning_effort"], "high")
        for heading in ("## Implementation Report", "## Fix Report", "## Blocked Report"):
            self.assertIn(heading, instructions)
        for rule in (
            "Do not spawn subagents or delegate work.",
            "Do not write tests; that is the job of `tester`.",
            "Do not refactor in fix mode",
            "report the visual check as not performed",
        ):
            self.assertIn(rule, instructions)
        for copilot_only in ("askQuestions", "ddg-search", "vscode"):
            self.assertNotIn(copilot_only, instructions)

    def test_rubber_duck_and_tester_names(self) -> None:
        rubber = tomllib.loads((self.resources / "agents" / "rubber-duck.toml").read_text(encoding="utf-8"))
        tester = tomllib.loads((self.resources / "agents" / "tester.toml").read_text(encoding="utf-8"))
        self.assertEqual(rubber["name"], "rubber_duck")
        self.assertEqual(tester["name"], "tester")


class DeliverySkillTests(support.TargetMixin):
    def setUp(self) -> None:
        super().setUp()
        self.skill_dir = self.resources / "skills" / "orchestrated-delivery"
        self.frontmatter, self.body = parse_frontmatter(
            (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        )

    def test_frontmatter(self) -> None:  # SC-5
        self.assertEqual(self.frontmatter.get("name"), "orchestrated-delivery")
        self.assertTrue(self.frontmatter.get("description"))

    def test_body_contains_routes(self) -> None:  # SC-5 / SC-7
        for route in ("trivial", "bug-fix", "review", "test-only", "docs", "standard"):
            self.assertIn(f"`{route}`", self.body, f"route {route} missing")

    def test_body_contains_loop_limits(self) -> None:  # SC-5 / SC-7
        self.assertIn("maximum of 2 cycles", self.body)
        self.assertIn("maximum of 3 cycles", self.body)

    def test_body_contains_phase_gating(self) -> None:  # SC-7
        for phase in ("Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5"):
            self.assertIn(phase, self.body)

    def test_body_contains_never_stop_clause(self) -> None:  # SC-5 / SC-7
        self.assertIn("a structured user question", self.body)
        self.assertNotIn("askQuestions", self.body)
        self.assertIn("Stopping is a failure state", self.body)
        self.assertIn("free-text option", self.body)

    def test_body_contains_delegation_doctrine(self) -> None:  # SC-7
        self.assertIn("orchestrate only", self.body.lower())
        self.assertIn("Implementation is delegated to `developer`", self.body)
        self.assertNotRegex(self.body, r"\bworker\b")

    def test_body_contains_prompt_contract(self) -> None:  # SC-7
        for field in ("Acceptance Criteria", "UI Affected", "Docs Affected", "Expected Output"):
            self.assertIn(field, self.body)



class CodexDeliverySkillTests(DeliverySkillTests, unittest.TestCase):
    target_name = "codex"

    def test_explicit_invocation_only(self) -> None:
        metadata = (self.skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("policy:\n  allow_implicit_invocation: false\n", metadata)

    def test_context_and_waiting_discipline(self) -> None:
        normalized = " ".join(self.body.split())
        self.assertIn('fork_turns: "none"', normalized)
        self.assertIn("self-contained scoped digest", normalized)
        self.assertIn("not filesystem access or permissions", normalized)
        self.assertIn("do not wait or poll subagents", normalized)
        self.assertIn("one long `wait_agent` call (5–10 minutes)", normalized)
        self.assertIn("A timeout alone does not mean a subagent is stalled.", normalized)

    def test_body_contains_model_routing(self) -> None:  # SC-7
        for model in ("gpt-6-sol", "gpt-5.6-terra", "gpt-6-luna"):
            self.assertIn(model, self.body)
        normalized = " ".join(self.body.split())
        self.assertIn(
            "`gpt-6-sol` for demanding planning, design, and holistic review "
            "(`final_reviewer`, `rubber_duck`, `spec_designer`, and `ui_designer`)",
            normalized,
        )
        self.assertIn("(`developer` and `tester`)", normalized)
        self.assertNotRegex(
            self.body,
            r"(?<![-.\w])gpt-5\.6(?![-.\w])",
            "model-routing guidance must not name the unsupported unsuffixed gpt-5.6 model",
        )


class ClaudeDeliverySkillTests(DeliverySkillTests, unittest.TestCase):
    target_name = "claude"

    def test_explicit_invocation_only(self) -> None:
        self.assertEqual(self.frontmatter.get("disable-model-invocation"), "true")
        self.assertFalse((self.skill_dir / "agents").exists())

    def test_context_and_waiting_discipline(self) -> None:
        normalized = " ".join(self.body.split())
        self.assertIn("Subagents start with fresh context", normalized)
        self.assertIn("not filesystem access or permissions", normalized)
        self.assertIn("never poll, sleep, or check status in a loop", normalized)
        self.assertIn("the AskUserQuestion tool", normalized)

    def test_body_contains_model_routing(self) -> None:
        normalized = " ".join(self.body.split())
        self.assertIn(
            "`opus` for demanding planning, design, and holistic review "
            "(`final-reviewer`, `rubber-duck`, `spec-designer`, and `ui-designer`)",
            normalized,
        )
        self.assertIn("`sonnet` for implementation and test work (`developer` and `tester`)", normalized)
        self.assertIn("`haiku` for narrow, fast reconnaissance (`discovery`)", normalized)

class CodeReviewSkillTests(support.TargetMixin):
    def setUp(self) -> None:
        super().setUp()
        self.skill_dir = self.resources / "skills" / "orchestrated-code-review"
        self.checklist_path = self.skill_dir / "references" / "review-checklist-template.md"
        self.frontmatter, self.body = parse_frontmatter(
            (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        )
        self.normalized_body = " ".join(self.body.split())

    def test_frontmatter_and_explicit_invocation_policy(self) -> None:
        invocation = f"{self.vocabulary['invoke']}orchestrated-code-review"
        self.assertEqual(self.frontmatter.get("name"), "orchestrated-code-review")
        self.assertIn(
            f"explicitly invokes {invocation}", self.frontmatter.get("description", "")
        )
        if self.skill_metadata:
            metadata = (self.skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
            self.assertIn("default_prompt:", metadata)
            self.assertIn(invocation, metadata)
            self.assertIn("policy:\n  allow_implicit_invocation: false\n", metadata)
        else:
            self.assertEqual(self.frontmatter.get("disable-model-invocation"), "true")

    def test_review_scope_and_authorization_boundaries(self) -> None:
        self.assertIn("code-review.md", self.body)
        self.assertIn("current pull request", self.body)
        self.assertIn("working-tree diff", self.body)
        self.assertIn("existing comments", self.body)
        self.assertIn("do not change production code or tests", self.normalized_body)
        self.assertIn("do not", self.body.lower())
        self.assertIn("publish review comments", self.body)

    def test_review_categories_and_false_positive_controls(self) -> None:
        for concern in (
            "Security",
            "Correctness",
            "Performance",
            "Test coverage",
            "architecture",
            "Maintainability",
            "documentation",
        ):
            self.assertIn(concern, self.body)
        self.assertIn("TODO/FIXME", self.body)
        self.assertIn("suppressions", self.body)
        self.assertIn("false positives", self.body)
        self.assertIn("Report every independently supported, actionable defect", self.body)
        self.assertIn("Do not target a minimum or maximum number of findings", self.body)
        self.assertIn("do not report the mere presence of a suppression", self.normalized_body)

    def test_mandatory_file_backed_checklist_preserves_original_review_stages(self) -> None:
        checklist = self.checklist_path.read_text(encoding="utf-8")
        self.assertIn("mandatory for every review", self.normalized_body)
        self.assertIn(".agent-work/code-review-checklist.md", self.normalized_body)
        self.assertIn("in-progress", self.normalized_body)
        self.assertIn("completed", self.normalized_body)
        numbered_steps = [
            line for line in checklist.splitlines() if line.startswith(tuple(f"| {i} " for i in range(1, 11)))
        ]
        self.assertEqual(len(numbered_steps), 10)
        for step in (
            "Initialize review context",
            "Security and critical issues",
            "Performance and logic",
            "Code quality and standards",
            "Testing and documentation",
            "Architecture and dependencies",
            "Maintainability and best practices",
            "Verify suggested changes",
            "Generate provisional review document",
            "Verify findings and finalize",
        ):
            self.assertIn(step, checklist)

    def test_requires_independent_validation_with_per_finding_verdicts(self) -> None:
        self.assertIn(
            "For every candidate finding, create a separate validation assignment",
            self.normalized_body,
        )
        self.assertIn("a read-only review subagent", self.normalized_body)
        self.assertIn("independent of the primary reviewer", self.normalized_body)
        self.assertIn("exactly one candidate claim", self.normalized_body)
        self.assertIn("may receive multiple assignments", self.normalized_body)
        self.assertIn("distribute assignments among them", self.normalized_body)
        self.assertIn("Prefer a fresh validator for critical", self.normalized_body)
        self.assertIn(
            "individual verdict and brief evidence for every claim", self.normalized_body
        )
        checklist = " ".join(self.checklist_path.read_text(encoding="utf-8").split())
        self.assertIn("each finding must have a separate assignment, verdict, and evidence", checklist)

    def test_provisional_draft_precedes_validation_and_finalization(self) -> None:
        checklist = " ".join(self.checklist_path.read_text(encoding="utf-8").split())
        self.assertIn("provisional `code-review.md` draft", self.normalized_body)
        self.assertIn("label the draft and every candidate as unverified", self.normalized_body)
        self.assertIn("before retaining candidates in the finalized report", self.normalized_body)
        self.assertIn("remove its provisional labels", self.normalized_body)
        self.assertLess(
            checklist.index("Generate provisional review document"),
            checklist.index("Verify findings and finalize"),
        )

    def test_does_not_depend_on_copilot_only_tools(self) -> None:
        for unavailable_tool in (
            "manage_todo_list",
            "runSubagent",
            "activePullRequest",
            "openPullRequest",
            "github.vscode-pull-request-github",
        ):
            self.assertNotIn(unavailable_tool, self.body)


class CodexCodeReviewSkillTests(CodeReviewSkillTests, unittest.TestCase):
    target_name = "codex"


class ClaudeCodeReviewSkillTests(CodeReviewSkillTests, unittest.TestCase):
    target_name = "claude"


class CodexDiscoverabilityTests(support.TargetMixin, unittest.TestCase):
    target_name = "codex"

    def test_skill_root_under_documented_codex_skills_root(self) -> None:  # SC-8
        skill_root = cli.resolve_skill_root()
        documented = (
            Path.home() / ".agents" / "skills",
            Path(".agents") / "skills",
            Path("/etc/codex/skills"),
        )
        self.assertTrue(
            any(
                skill_root == root or root in skill_root.parents
                for root in documented
            ),
            f"resolved skill root {skill_root} is not a documented Codex skills root",
        )
        # The skill installs directly under the documented root.
        self.assertEqual(skill_root, Path.home() / ".agents" / "skills")

    def test_agents_root_independent_of_skill_root(self) -> None:  # SC-2
        self.assertNotEqual(cli.resolve_agents_root(), cli.resolve_skill_root())


# A project policy: agents pin a model alias; Claude Code also accepts fable and full IDs.
CLAUDE_EXPECTED_MODELS = {
    "developer": "sonnet",
    "discovery": "haiku",
    "final-reviewer": "opus",
    "rubber-duck": "opus",
    "spec-designer": "opus",
    "tester": "sonnet",
    "ui-designer": "opus",
}
CLAUDE_EXPECTED_EFFORT = {name: "high" for name in CLAUDE_EXPECTED_MODELS} | {"discovery": "xhigh"}
CLAUDE_READ_ONLY_AGENTS = {"discovery", "final-reviewer", "rubber-duck"}


def split_yaml_frontmatter(text: str) -> tuple[dict[str, object], str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if match is None:
        raise AssertionError("missing YAML frontmatter")
    return yaml.safe_load(match.group(1)), text[match.end():]


class ClaudeAgentPayloadTests(support.TargetMixin, unittest.TestCase):
    target_name = "claude"

    def agents(self) -> dict[str, tuple[dict[str, object], str]]:
        return {
            path.stem: split_yaml_frontmatter(path.read_text(encoding="utf-8"))
            for path in sorted((self.resources / "agents").glob("*.md"))
        }

    def test_expected_agents_present(self) -> None:
        self.assertEqual(set(self.agents()), set(CLAUDE_EXPECTED_MODELS))

    def test_frontmatter_schema(self) -> None:
        for stem, (meta, body) in self.agents().items():
            with self.subTest(agent=stem):
                self.assertEqual(
                    set(meta), {"name", "description", "model", "effort", "disallowedTools"}
                )
                self.assertEqual(meta["name"], stem)
                self.assertRegex(stem, r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
                self.assertTrue(str(meta["description"]).strip())
                self.assertEqual(meta["model"], CLAUDE_EXPECTED_MODELS[stem])
                self.assertEqual(meta["effort"], CLAUDE_EXPECTED_EFFORT[stem])
                self.assertTrue(body.strip())

    def test_no_agent_spawns_subagents_and_read_only_agents_cannot_edit(self) -> None:
        for stem, (meta, _) in self.agents().items():
            with self.subTest(agent=stem):
                expected = "Agent, Edit, Write, NotebookEdit" if stem in CLAUDE_READ_ONLY_AGENTS else "Agent"
                self.assertEqual(meta["disallowedTools"], expected)


if __name__ == "__main__":
    unittest.main()
