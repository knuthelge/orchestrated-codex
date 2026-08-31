# Verification report template

A report is well-formed only when it opens with the `PASS`/`FAIL` verdict and carries every
required field below — the requirement-by-requirement evidence, checks, and findings. The
orchestrator reprompts only when a required field is missing, not on a well-formed FAIL.

```markdown
## Verification: PASS | FAIL

### Requirements
- REQ-1: met | not met — <file:line or runtime evidence>

### Checks
- `<command or browser flow>`: passed | failed | not run — <result or reason>

### Documentation
- <path>: correct | issue — <evidence>

### Visual verification
- <route and states inspected>: <result>

### Findings
- critical | major | minor — `<file:line>` — <impact> — Fix: <focused suggestion>

### Residual risks
- <risk not disproven by available checks, or none>
```
