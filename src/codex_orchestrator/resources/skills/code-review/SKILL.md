---
name: code-review
description: Review a code change and write a prioritized, independently verified code-review.md report. Use only when the user explicitly invokes $code-review.
---

# Code review

Review the requested diff, commit range, branch, pull request, or working-tree changes and
write the result to `code-review.md` in the project root. Reviewing authorizes creation or
update of that report and `.agent-work/code-review-checklist.md` only: do not change
production code or tests, publish review comments, approve a pull request, or request changes
unless the user separately asks for that action.

## Create and maintain the review checklist

Before inspecting the change, create `.agent-work/code-review-checklist.md` from
[references/review-checklist-template.md](references/review-checklist-template.md). This is
mandatory for every review, regardless of size. Fill in the review scope and keep all ten
steps. Set a step to `in-progress` immediately before starting it and to `completed`
immediately after finishing it; never mark work complete in advance. Keep the checklist after
the review as evidence of the coverage and finding-validation gates that ran.

If a planning mechanism is also available in the current Codex environment, it may mirror
the file-backed checklist, but it does not replace it. Do not depend on Copilot-specific todo
or extension tools.

## Establish the review scope

- Resolve exactly what is being reviewed. Prefer the scope named by the user; otherwise use
  the current pull request when connected PR context is available, or the current
  working-tree diff.
- Inspect the complete diff and the surrounding source needed to evaluate it. Read project
  instructions, coding standards, tests, and relevant architecture documentation.
- When a pull request or prior review is available through connected tools or a local CLI,
  inspect its description, changed files, existing comments, reviews, and discussion. Use
  that context to avoid duplicating resolved or already-reported feedback. Do not require a
  GitHub connection when the local repository contains enough context.
- Inspect warning, lint, and type suppressions added or affected by the change. Report a
  suppression only when there is concrete evidence that it hides a defect or creates a
  material maintenance risk; do not report the mere presence of a suppression.
- Locate TODO/FIXME comments added or affected by the change and determine whether they hide
  incomplete behavior, deferred correctness work, or required follow-up.

## Analyze the change

Prioritize defects that materially affect users or maintainers:

1. Security, privacy, authorization, input validation, secrets, and unsafe data handling.
2. Correctness, runtime failures, edge cases, error handling, resource lifetime, and
   concurrency.
3. Performance regressions, avoidable I/O, inefficient algorithms, memory use, and query
   patterns.
4. Test coverage and test quality for changed behavior, including failure paths and
   regressions.
5. API and architecture compatibility, dependencies, coupling, separation of concerns, and
   consistency with project conventions.
6. Maintainability: complexity, duplication, readability, documentation, and technical debt.

Distinguish concrete defects from questions, optional refactors, and future observations.
Avoid speculative feedback and stylistic preferences already enforced by formatters or
linters. Report every independently supported, actionable defect worth the author's
attention. Do not target a minimum or maximum number of findings, and do not omit a valid
finding merely to keep the report short. Include positive feedback only when it adds useful
context.

Step 9 may place candidate findings in a provisional `code-review.md` draft, but it must label
the draft and every candidate as unverified. Complete independent validation before retaining
candidates in the finalized report or presenting them as verified. For every candidate
finding, create a separate validation assignment for a read-only review subagent that is
independent of the primary reviewer. Give the validator the diff, relevant context, and
exactly one candidate claim without revealing the intended verdict or instructing it to
agree. A validator may receive multiple assignments so it can reuse its understanding of the
change. When multiple validators are available, distribute assignments among them so one
validator is not the sole check for an entire multi-finding review. Prefer a fresh validator
for critical, security-sensitive, or closely related claims where a shared mistaken
assumption could affect multiple verdicts. Require an individual verdict and brief evidence
for every claim, and record them in the checklist's finding-validation table. Never let the
primary reviewer substitute its own second pass for independent validation.

During step 10, remove findings that the validator confirms are false positives and downgrade
or omit claims that remain uncertain. Then finalize `code-review.md` and remove its provisional
labels. Never modify code to prove a finding. If subagents are unavailable, pause and ask
whether the user wants an explicitly unverified review; do not describe a self-reviewed
fallback as independently verified.

## Write `code-review.md`

Lead with an executive summary, the reviewed scope, and one recommendation: `approve`,
`comment`, or `request changes`, with a short rationale. Then list findings from highest to
lowest priority. Use these priority labels:

- 🔥 Critical
- ⚠️ High
- 🟡 Medium
- 🟢 Low

For each finding, include:

- a concise action marker such as 🔧 change request, ❓ question, ♻️ refactor, 💭 concern,
  ⛏️ nitpick, 🌱 future observation, or 👍 positive feedback;
- priority and a precise repository-relative file path with line or diff location;
- the observable problem, impact, and reasoning;
- a concrete suggested change, with a short code example only when it materially clarifies
  the fix; and
- relevant standards or documentation when they strengthen the recommendation.

If no actionable defects remain after verification, say so clearly and note meaningful
coverage gaps or residual risks without inventing findings. End with a compact summary of
finding counts by priority, verification performed, and any assumptions or inaccessible
context.

Return a concise message identifying the report path and overall recommendation. Keep all
review details in `code-review.md`.
