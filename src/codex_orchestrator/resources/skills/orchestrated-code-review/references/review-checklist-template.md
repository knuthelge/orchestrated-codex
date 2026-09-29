# Code review checklist

Review scope: _fill in before starting_

Use exactly these status values: `pending`, `in-progress`, and `completed`. Change each step
to `in-progress` immediately before starting it and to `completed` immediately after it is
finished.

| # | Required step | Required coverage | Status |
| -: | --- | --- | --- |
| 1 | Initialize review context | Resolve scope; inspect PR details, changed files, existing review comments, languages, and frameworks when available. | pending |
| 2 | Security and critical issues | Check security, privacy, authorization, secrets, input validation, runtime failures, null handling, and edge cases. | pending |
| 3 | Performance and logic | Check algorithms, I/O and query patterns, memory/resource use, concurrency, conditionals, loops, and error handling. | pending |
| 4 | Code quality and standards | Check project conventions, naming, organization, duplication, readability, and complexity. | pending |
| 5 | Testing and documentation | Check test coverage and quality, failure paths, regressions, documentation, and affected TODO/FIXME comments. | pending |
| 6 | Architecture and dependencies | Check dependencies, compatibility, architectural patterns, separation of concerns, and coupling. | pending |
| 7 | Maintainability and best practices | Check technical debt, hardcoded values, extensibility, reuse, and language/framework practices. | pending |
| 8 | Verify suggested changes | Re-check candidate claims and ensure each suggestion is accurate, actionable, and an actual improvement. | pending |
| 9 | Generate provisional review document | Draft `code-review.md` with its executive summary, recommendation, prioritized candidate findings, paths, evidence, and suggested changes; label the draft and candidates as unverified. | pending |
| 10 | Verify findings and finalize | Validate every candidate through a separate read-only subagent assignment. Reuse validator context where useful, distribute assignments across available validators, and record an individual verdict and evidence for each claim before finalizing. | pending |

## Finding validation

Add one row per candidate finding. A validator may appear in multiple rows, but each finding
must have a separate assignment, verdict, and evidence.

| Finding ID | Candidate claim | Validator | Verdict | Evidence | Status |
| --- | --- | --- | --- | --- | --- |
