# PRD and technical design template

```markdown
# PRD: <title>

## Goal
<observable definition of done>

## Scope
### In scope
- <item>
### Out of scope
- <item>

## Requirements
- REQ-1: <testable behavior>

## Success criteria
- SC-1: <measurable verification>

## Technical design
### Relevant context
<discovery evidence and constraints>

### File and interface changes
- `<path or interface>`: <change and rationale>

### Documentation changes
- User-facing: <observable usage or behavior>
- Developer-facing: <architecture or maintenance detail>

### Implementation steps
1. <bounded step> — Acceptance: <specific evidence>

### Hand-off digest
- REQ-1: <=40-line self-contained slice — target requirement, its acceptance criteria, and
  the files/interfaces it touches — the orchestrator lifts for each worker/tester hand-off.

### Edge cases and failure behavior
- <case>: <handling>

### Dependencies and compatibility
- <dependency, migration, or compatibility constraint>

## Risks and rollback
- <risk and mitigation>

## Open questions
- <only questions whose answers materially affect the plan>
```
