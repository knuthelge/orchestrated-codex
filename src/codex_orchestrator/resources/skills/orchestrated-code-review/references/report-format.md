# Code review report format

Use this format when drafting and finalizing `code-review.md`. Optimize the report for
scanning: use short sections and labeled bullet points, and do not write a finding as multiple
dense paragraphs.

## Report structure

```markdown
# Code Review: <reviewed change>

## Executive summary

- **Recommendation:** `approve`, `comment`, or `request changes`
- **Rationale:** <one or two sentences>
- **Scope:** <pull request, branch, commit range, or working-tree diff>
- **Findings:** <count by priority>
- **Main risk:** <most important remaining risk, or `None identified`>

## Findings

### <action marker> F1 — <concise, actionable title>

- **Priority:** <priority label>
- **Location:** `relative/path.ext:line`
- **Issue:** <observable problem in one or two sentences>
- **Impact:**
  - <concrete user, runtime, security, performance, or maintenance consequence>
- **Evidence / why:**
  - <relevant execution path or behavior>
  - <condition that triggers the problem>
- **Recommended change:**
  - <smallest practical correction>
- **Example:** <optional short code example when it materially clarifies the correction>
- **Validation:** Independently verified — <one concise reproduction, test result, or
  corroborating fact; do not name the validator>

## Non-blocking observations

<Questions, optional refactors, future observations, and useful positive feedback.>

## Summary

| Priority | Count |
| --- | ---: |
| 🔥 Critical | 0 |
| ⚠️ High | 0 |
| 🟡 Medium | 0 |
| 🟢 Low | 0 |

- **Verification performed:** <concise summary>
- **Coverage gaps:** <tests or context that could not be inspected>
- **Assumptions:** <material assumptions, or `None`>
```

## Priority labels

Assign priority from both impact and realistic likelihood. Consider affected users, exposure,
recoverability, whether the behavior is on a supported or common path, and the preconditions
needed to trigger it. Reproducing a defect increases confidence that it is real; it does not
by itself increase its severity.

- 🔥 Critical: likely or readily exploitable behavior with catastrophic consequences, such as
  broad compromise, irreversible data loss, or a widespread outage.
- ⚠️ High: serious user, security, or operational harm on a supported or realistically reached
  path. The defect will usually block merging.
- 🟡 Medium: a material defect with limited scope, less common preconditions, or a recoverable
  impact. It should be fixed, but does not ordinarily block merging by itself.
- 🟢 Low: a minor robustness or maintainability problem, rare misconfiguration, or small
  localized impact.

State material preconditions in Evidence / why. Do not inflate priority merely because a
finding involves security-sensitive code, a safety check, malformed local state, or an
independently reproduced failure. If the threat model or expected behavior is unclear, use a
question or concern instead of asserting a high-priority defect.

## Action markers

Use an action marker in each item title to show what response is expected:

- 🔧 change request: a verified defect that needs to be corrected;
- ❓ question: a fully formed question that needs an answer;
- ♻️ refactor: a worthwhile but non-blocking structural improvement;
- 💭 concern: a risk or alternative that needs discussion;
- ⛏️ nitpick: an optional minor suggestion that is usually better omitted;
- 🌱 future observation: a non-blocking consideration for later; and
- 👍 positive feedback: a particularly strong choice worth highlighting.

## Finding rules

- List verified actionable defects from highest to lowest priority.
- Use one idea per bullet and keep each bullet to one or two sentences unless a short sequence
  or code example requires more.
- Keep Issue, Impact, Evidence / why, Recommended change, and Validation as separate labeled
  fields. Do not combine them into a prose narrative.
- Make the title actionable and specific enough to understand without opening the file.
- Use a precise repository-relative path with the narrowest useful line or diff location.
- Recommend the smallest practical correction. Include Example only when a short code sample
  materially clarifies the fix. Cite standards or documentation only when they strengthen
  the recommendation.
- Keep Validation to one concise sentence containing concrete corroboration: a reproduction,
  focused test result, observed execution path, or independent evidence. Do not name the
  validator, describe the validation process, or repeat Evidence / why.
- Report every independently supported, actionable defect worth the author's attention, but
  omit marginal stylistic advice and keep optional material out of Findings.
- Put questions, optional refactors, future observations, and positive feedback under
  Non-blocking observations. Do not include them in defect counts or let them determine the
  recommendation.

## Recommendation

Choose the recommendation based on merge risk:

- `request changes` when at least one verified defect presents unacceptable merge risk,
  ordinarily a Critical or High issue or a Medium issue on a core supported path;
- `comment` when the findings are actionable but non-blocking; or
- `approve` when no verified actionable defects remain.

Do not derive the recommendation mechanically from finding counts or from the fact that a
defect was reproduced. Explain the concrete merge risk in the rationale.

If no actionable defects remain after verification, say so clearly. Keep the empty Findings
section brief and report meaningful coverage gaps or residual risks without inventing
findings. End with the Summary table and concise notes about verification, assumptions, and
inaccessible context.
