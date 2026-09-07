"""Payload correctness tests: agent TOML schema/model set, SKILL.md content, discoverability.

These assert the shipped resources are well-formed (valid TOML, allowed models, and
``sandbox_mode`` used only to pin read-only agents), that SKILL.md carries the orchestration
doctrine, and that the resolved skill install path lies under a documented Codex skills root.
Standard library only.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from codex_orchestrator import cli


EXPECTED_MODELS = {
    "discovery": "gpt-5.6-luna",
    "final_reviewer": "gpt-5.6-sol",
    "rubber_duck": "gpt-5.6-sol",
    "spec_designer": "gpt-5.6-sol",
    "tester": "gpt-5.6-terra",
    "ui_designer": "gpt-5.6-terra",
}
READ_ONLY_AGENTS = {"discovery", "final_reviewer", "rubber_duck"}
WRITE_AGENTS = {"spec_designer", "ui_designer", "tester"}
AGENTS_DIR = cli.RESOURCE_ROOT / "agents"
SKILL_MD = cli.RESOURCE_ROOT / "skills" / "orchestrated-delivery" / "SKILL.md"
SKILL_OPENAI_YAML = (
    cli.RESOURCE_ROOT / "skills" / "orchestrated-delivery" / "agents" / "openai.yaml"
)
CODE_REVIEW_SKILL_MD = cli.RESOURCE_ROOT / "skills" / "code-review" / "SKILL.md"
CODE_REVIEW_OPENAI_YAML = (
    cli.RESOURCE_ROOT / "skills" / "code-review" / "agents" / "openai.yaml"
)
CODE_REVIEW_CHECKLIST = (
    cli.RESOURCE_ROOT
    / "skills"
    / "code-review"
    / "references"
    / "review-checklist-template.md"
)


def agent_toml_paths() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.toml"))


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


class AgentPayloadTests(unittest.TestCase):
    def test_expected_agent_files_present(self) -> None:
        names = {path.name for path in agent_toml_paths()}
        self.assertEqual(
            names,
            {
                "discovery.toml",
                "spec-designer.toml",
                "rubber-duck.toml",
                "ui-designer.toml",
                "tester.toml",
                "final-reviewer.toml",
            },
        )
        self.assertNotIn("verifier.toml", names)

    def test_agent_model_routing(self) -> None:  # SC-3
        actual = {}
        for path in agent_toml_paths():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            actual[data["name"]] = data.get("model")
        self.assertEqual(actual, EXPECTED_MODELS)

    def test_schema_and_sandbox_mode(self) -> None:  # SC-4
        for path in agent_toml_paths():
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
        for path in agent_toml_paths():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if data["name"] in READ_ONLY_AGENTS:
                seen.add(data["name"])
                self.assertEqual(
                    data.get("sandbox_mode"),
                    "read-only",
                    f"{path.name} must pin sandbox_mode = 'read-only'",
                )
        self.assertEqual(seen, READ_ONLY_AGENTS)

    def test_rubber_duck_and_tester_names(self) -> None:
        rubber = tomllib.loads((AGENTS_DIR / "rubber-duck.toml").read_text(encoding="utf-8"))
        tester = tomllib.loads((AGENTS_DIR / "tester.toml").read_text(encoding="utf-8"))
        self.assertEqual(rubber["name"], "rubber_duck")
        self.assertEqual(tester["name"], "tester")


class SkillPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frontmatter, self.body = parse_frontmatter(
            SKILL_MD.read_text(encoding="utf-8")
        )

    def test_frontmatter(self) -> None:  # SC-5
        self.assertEqual(self.frontmatter.get("name"), "orchestrated-delivery")
        self.assertTrue(self.frontmatter.get("description"))

    def test_explicit_invocation_only(self) -> None:
        metadata = SKILL_OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn("policy:\n  allow_implicit_invocation: false\n", metadata)

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
        self.assertIn("askQuestions", self.body)
        self.assertIn("Stopping is a failure state", self.body)
        self.assertIn("free-text option", self.body)

    def test_body_contains_delegation_doctrine(self) -> None:  # SC-7
        self.assertIn("orchestrate only", self.body.lower())
        self.assertIn("worker", self.body)

    def test_body_contains_model_routing(self) -> None:  # SC-7
        for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
            self.assertIn(model, self.body)
        self.assertNotRegex(
            self.body,
            r"(?<![-.\w])gpt-5\.6(?![-.\w])",
            "model-routing guidance must not name the unsupported unsuffixed gpt-5.6 model",
        )

    def test_body_contains_prompt_contract(self) -> None:  # SC-7
        for field in ("Acceptance Criteria", "UI Affected", "Docs Affected", "Expected Output"):
            self.assertIn(field, self.body)


class CodeReviewSkillPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frontmatter, self.body = parse_frontmatter(
            CODE_REVIEW_SKILL_MD.read_text(encoding="utf-8")
        )
        self.normalized_body = " ".join(self.body.split())

    def test_frontmatter_and_explicit_invocation_policy(self) -> None:
        self.assertEqual(self.frontmatter.get("name"), "code-review")
        self.assertIn("explicitly invokes $code-review", self.frontmatter.get("description", ""))
        metadata = CODE_REVIEW_OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn("default_prompt:", metadata)
        self.assertIn("$code-review", metadata)
        self.assertIn("policy:\n  allow_implicit_invocation: false\n", metadata)

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
        self.assertIn("Keep the findings set restrained", self.body)

    def test_mandatory_file_backed_checklist_preserves_original_review_stages(self) -> None:
        checklist = CODE_REVIEW_CHECKLIST.read_text(encoding="utf-8")
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

    def test_requires_one_distinct_independent_validator_per_finding(self) -> None:
        self.assertIn("one distinct read-only review subagent", self.normalized_body)
        self.assertIn("exactly one candidate claim", self.normalized_body)
        self.assertIn("Never batch multiple findings", self.normalized_body)
        self.assertIn(
            "validator identity, verdict, and brief evidence", self.normalized_body
        )
        self.assertNotIn("Batch related findings", self.body)

    def test_provisional_draft_precedes_validation_and_finalization(self) -> None:
        checklist = " ".join(CODE_REVIEW_CHECKLIST.read_text(encoding="utf-8").split())
        self.assertIn("provisional `code-review.md` draft", self.normalized_body)
        self.assertIn("label the draft and every candidate as unverified", self.normalized_body)
        self.assertIn("before retaining it in the finalized report", self.normalized_body)
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


class DiscoverabilityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
