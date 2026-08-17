---
name: orchestrated-delivery
description: Deliver software changes through an adaptive workflow of task classification, codebase discovery, requirements and technical design, implementation, testing, UI verification, and final review. Use for feature implementation, bug fixes, refactors, code or PR reviews, test-only work, documentation changes, or complex multi-step engineering tasks where Codex should coordinate specialized subagents and maintain clear acceptance criteria.
---

# Orchestrated delivery

Lead the work from the primary thread. Use subagents to isolate specialized or independently useful work, not as mandatory ceremony. Keep user intent and final responsibility in the primary thread.

## Classify the request

Choose the lightest sufficient route:

- `trivial`: make the obvious scoped change, then verify it.
- `bug-fix`: diagnose the execution path, implement the smallest root-cause fix, then verify it.
- `review`: inspect and report findings; do not modify unless explicitly requested.
- `test-only`: add or repair tests without changing production behavior.
- `docs`: update documentation, then verify accuracy, links, and formatting.
- `standard`: use discovery, design, implementation, verification, and risk-based final review.

Upgrade to `standard` when discovery reveals architectural choices, broad impact, or significant UI design decisions. Do not downgrade solely to save effort.

## Run the route

### Discovery

Delegate to the `discovery` custom agent when the relevant code path, conventions, impact, or version-specific behavior is not already clear. Provide the user request, repository scope, and specific questions. Ask for evidence, not solutions.

Parallelize only independent read-only investigations. Do not spawn multiple agents to rediscover the same code.

### Requirements and design

For `standard` work, delegate to `spec_designer` after discovery. Store substantial plans in `.agent-work/prd.md` using [references/prd-template.md](references/prd-template.md). Keep small plans inline.

Require every behavior to be testable and every implementation step to have acceptance criteria. Have the designer adversarially review its own plan. Ask the user to approve only when the plan contains meaningful product, scope, architectural, destructive, or costly choices.

For substantial visual design work, delegate to `ui_designer` after requirements stabilize. Skip it for routine UI fixes that follow established components and tokens.

### Implementation

Implement cohesive changes in the primary thread or delegate bounded independent units to the built-in worker. Preserve unrelated user edits. Follow repository instructions and the approved plan, but adapt when the code provides contrary evidence and record the reason.

Do not parallelize agents that would edit the same files or tightly coupled interfaces. Run formatting, linting, type checks, and focused tests during implementation.

### Verification

Delegate independent validation to `verifier` for behavior changes. Provide the original request, acceptance criteria, changed-file scope, and any plan or visual-spec paths. Use [references/verification-template.md](references/verification-template.md) for expected reporting.

On failure, consolidate findings into one prioritized fix set. Apply root-cause fixes, then re-run the failed checks. After two unsuccessful correction cycles, reassess the approach and seek user input only when a missing decision prevents further safe progress.

### Final review

Delegate to `final_reviewer` for substantial, security-sensitive, cross-cutting, or explicitly requested work. Skip a separate final review for trivial and low-risk changes already independently verified.

Do not declare completion until explicit requirements are met and relevant checks pass, or clearly disclose checks that could not run and why.

## Maintain artifacts

Use `.agent-work/` only when artifacts materially aid a multi-step task. Do not create workflow documents for trivial work. Preserve `discovery.md` and `prd.md` as useful project records; remove disposable previews when the user approves cleanup.

End with the outcome, changed files, verification evidence, and any residual risks or decisions.
