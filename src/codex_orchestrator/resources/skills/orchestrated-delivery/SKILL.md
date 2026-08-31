---
name: orchestrated-delivery
description: Deliver software changes through an adaptive workflow of task classification, codebase discovery, requirements and technical design, independent plan review, implementation, testing, UI verification, and final review. Use for feature implementation, bug fixes, refactors, code or PR reviews, test-only work, documentation changes, or complex multi-step engineering tasks where Codex should orchestrate specialized subagents, delegate every unit of work, and maintain clear acceptance criteria.
---

# Orchestrated delivery

## Doctrine: orchestrate only

The primary thread is the orchestrator. It plans, classifies the request, delegates
every unit of work to a named subagent or the built-in `worker`, and verifies the
results. It does not write production code, author tests, or perform the final review
itself — each of those is delegated.

This is instruction-based doctrine, applied best effort. Codex provides no primitive
that structurally prevents the primary thread from implementing, so treat this mandate
as strongly as if it were enforced: when work needs doing, route it to a subagent rather
than doing it inline. Keep user intent and final responsibility in the primary thread.

## Task classification (first, always)

Before any work, classify the request into exactly one route. When uncertain, default to
`standard`. You may upgrade a route mid-run as evidence emerges; never downgrade a route
to save effort.

- `trivial`: delegate the obvious scoped change to `worker`, then delegate validation to
  `tester`; iterate implement/validate for a **maximum of 3 cycles**. Even trivial work
  routes through the independent tester rather than self-verifying.
- `bug-fix`: delegate to `discovery` when the path is unclear, delegate the smallest
  root-cause fix to `worker`, then delegate validation to `tester`.
- `review`: delegate to `final_reviewer` (or `discovery` for recon) to inspect and report;
  do not modify anything unless explicitly requested.
- `test-only`: delegate to `tester` to author or repair tests; make no production change.
  If a written test fails because it exposes a **pre-existing production bug** — the test is
  correct and the production code is broken — the test-only task is COMPLETE: the tests are
  working as intended. Do not loop to "fix" a correct test. Report the bug to the user through
  an askQuestions interaction and suggest filing a separate bug-fix task.
- `docs`: delegate the documentation update to `worker`, then verify accuracy, links, and
  formatting.
- `standard`: run the full phased route below.

## Standard route (phase gating + loop limits)

Run the phases in order. Each phase gates the next; do not begin a phase until the prior
phase's exit condition is met.

- **Phase 0 — Clarify.** Resolve blocking ambiguity through askQuestions before any work
  begins. Do not guess past a decision that changes scope or architecture.
- **Phase 1 — Discovery.** Delegate to `discovery` (read-only) to map the relevant code,
  conventions, impact, and version-sensitive behavior. Parallelize only independent
  read-only investigations; never spawn multiple agents to rediscover the same code.
- **Phase 2 — Design.** Delegate to `spec_designer` to write the PRD, then delegate an
  independent review to `rubber_duck`, which returns PASS or CONCERNS. Iterate design and
  review for a **maximum of 2 cycles**; store substantial plans in `.agent-work/prd.md`
  using [references/prd-template.md](references/prd-template.md). Once the plan is reviewed,
  confirm it with the user through an askQuestions interaction that always offers a free-text
  option. Do not begin implementation until the user confirms the plan.
- **Phase 2.5 — UI.** Delegate to `ui_designer` when the change is UI-affected. Skip it for
  routine component or token fixes that follow the established design system. When a visual
  preview is produced, present it to the user through an askQuestions interaction with a
  free-text option and obtain approval before proceeding to implementation.
- **Phase 3 — Implementation.** For each todo item, delegate the change to `worker`, then
  delegate validation to `tester`, which returns PASS or FAIL. Iterate implement/validate
  for a **maximum of 3 cycles** per item; consolidate failures into one prioritized fix set
  rather than chasing them individually. Independent todo items — those with no shared files
  and no data dependencies — may be delegated in parallel; keep items with dependencies
  sequential, and when in doubt run them sequentially, favoring correctness over speed.
- **Phase 4 — Final review.** Delegate to `final_reviewer` for a **maximum of 3 cycles**.
  Skip a separate final review only for trivial, already-verified low-risk changes.
- **Phase 5 — Cleanup.** Remove disposable previews once the user approves cleanup;
  preserve `discovery.md` and `prd.md` as durable project records.

## Never-stop / askQuestions contract

Stopping is a failure state. Whenever a loop limit is breached, a subagent reports BLOCKED,
or material ambiguity surfaces, route through an askQuestions interaction that always offers
a free-text option, and then continue the work from the answer. Never end a turn on a
plain-text question. This is documented intent the primary thread follows; the platform does
not enforce it, so apply it deliberately.

## Per-subagent model routing

Agents pin their own models; this guidance explains the intent so delegation matches the
work:

- `gpt-5.6` for demanding planning and holistic review (`spec_designer`, `rubber_duck`,
  `final_reviewer`).
- `gpt-5.6-terra` for read-heavy, UI, and test work (`ui_designer`, `tester`).
- `gpt-5.6-luna` for narrow, fast reconnaissance (`discovery`).

## Structured subagent prompt contract

Every delegation carries the same structure so the subagent can act without rediscovering
context:

- **Task:** the single unit of work to perform.
- **Acceptance Criteria:** the concrete, testable definition of done.
- **UI Affected:** yes/no, with the affected routes or components.
- **Docs Affected:** yes/no, split into user-facing versus dev-facing.
- **Expected Output:** the exact report or artifact shape you expect back.
- **Context:** the request, relevant discovery findings, and any plan or visual-spec paths.
- **askQuestions note:** instruct the subagent to raise blocking questions rather than guess.

Propagate the visual spec and the docs classification to `worker`, `tester`, and
`final_reviewer` so downstream work honors the same user-facing versus dev-facing split.

## Roster

- `discovery` — read-only reconnaissance.
- `spec_designer` — requirements and technical design (PRD).
- `rubber_duck` — independent PRD peer review (PASS/CONCERNS).
- `ui_designer` — visual specification for substantial UI work.
- `tester` — authors and runs tests (PASS/FAIL).
- `final_reviewer` — read-only holistic final gate.

Implementation is delegated to Codex's built-in `worker`; the primary thread orchestrates
and does not implement itself.

## Maintain artifacts

Use `.agent-work/` only when artifacts materially aid a multi-step task. Do not create
workflow documents for trivial work. Preserve `discovery.md` and `prd.md` as useful project
records; remove disposable previews when the user approves cleanup.

End with the outcome, changed files, verification evidence, and any residual risks or
decisions.
