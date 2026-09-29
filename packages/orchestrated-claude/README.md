# orchestrated-claude

A collection of Claude Code software delivery skills and focused subagents. It installs a
reusable orchestration workflow, an explicit code-review workflow, and custom subagents so
Claude Code can follow a structured process for discovery, planning, independent plan
review, implementation, testing, and final review. The delivery skill instructs the main
Claude Code conversation to orchestrate only: classify the request, delegate bounded work
when specialization, independent review, or parallelism materially improves the result,
and verify the result. The orchestrator hands each subagent a scoped digest of the plan and
a discovery impact map so work proceeds without re-reading whole artifacts.

The same workflow is available for Codex as
[`orchestrated-codex`](https://pypi.org/project/orchestrated-codex/).

## Installation

Install with [uv](https://docs.astral.sh/uv/):

```sh
uvx orchestrated-claude --install
```

The installer opens a guided checklist. Use the arrow keys to move, Space to select one or
more components, and Enter to install the selected set. Previously installed components are
preselected; deselecting one removes its unchanged files while preserving locally modified
files.

To run the current development version from the repository instead:

```sh
uvx --from "git+https://github.com/knuthelge/orchestrated-codex#subdirectory=packages/orchestrated-claude" orchestrated-claude --install
```

For CI or other unattended installs, install the bundled set or name a comma-separated
component set:

```sh
uvx orchestrated-claude --install --all
uvx orchestrated-claude --install --components code-review
uvx orchestrated-claude --install --components grill-me,teach
uvx orchestrated-claude --install --all --include-third-party
```

Named component installs automatically include dependencies declared by the component
registry. `--install --all` updates bundled components and makes no network request; it
leaves any previously installed third-party components in place without refreshing them.
Use `--components` to select specific third-party skills, or add `--include-third-party` to
`--all` to install or refresh every active third-party component. A plain `--install`
requires an interactive terminal.

### Optional third-party skills

The checklist also offers `grill-me`, `handoff`, and `teach` from
[Matt Pocock's skills repository](https://github.com/mattpocock/skills). These are optional
and are copied from `skills/productivity/` in that repository when selected. Selecting
`grill-me` also installs its required `grilling` companion skill. The checklist shows the
publisher, homepage, and licence before selection. Its MIT licence label is metadata
supplied by this package's registry, not a fresh verification of upstream licensing.

These choices track the repository's `main` branch, so selecting one trusts future upstream
content from that branch. A selected skill is fetched when you run an install that includes
it, and the installation manifest records the exact resolved commit SHA and source path for
every installed file. Deselecting a third-party skill removes its files only when they still
match the recorded hashes. The installer makes one unauthenticated GitHub API request to
resolve the branch, then downloads only the listed files from GitHub's raw-content host at
that commit; GitHub may rate-limit either service.

## Usage

Start a new Claude Code session after installation. Both workflows run only when you invoke
them; Claude does not select them automatically:

```text
/orchestrated-delivery implement this feature
/orchestrated-code-review review the current changes
```

`/orchestrated-code-review` writes its independently verified, prioritized findings to
`code-review.md`. It reviews local changes by default and uses pull-request context when
available. Every review also tracks the mandatory ten-stage workflow and its per-finding
validation in `.agent-work/code-review-checklist.md`. The review does not publish comments
or modify code unless you separately ask for that action. Claude Code's bundled
`/code-review` skill is unaffected.

The installer writes under the Claude Code configuration directory: `CLAUDE_CONFIG_DIR`
when it is set, and `~/.claude` otherwise.

- The **skills** install under `skills/`.
- The **subagents** install under `agents/` as Markdown files.
- The installation manifest is `claude-orchestrator-install.json` in the same directory.

Use `--claude-config-dir PATH` to install into another configuration directory.

## Uninstall

Remove the installed files with:

```sh
uvx orchestrated-claude --uninstall
```

The installer refuses to overwrite files it does not own. Uninstall removes only installed
files that still match the recorded hashes and then deletes the manifest; locally modified
installed files are preserved and reported.

## Installed components

The bundled components are:

- `orchestrated-delivery` (skill): task classification and an adaptive discovery, planning,
  independent plan review, implementation, testing, and final-review workflow.
- `orchestrated-code-review` (skill, component `code-review`): explicit-only review of a
  local diff or pull request, producing a comprehensive, prioritized, independently verified
  `code-review.md` report without a minimum or maximum finding count.
- `agents/discovery.md`: read-only codebase reconnaissance.
- `agents/spec-designer.md`: requirements and technical design.
- `agents/rubber-duck.md`: independent PRD peer review (PASS/CONCERNS).
- `agents/ui-designer.md`: visual design specification for substantial UI work.
- `agents/developer.md`: implements planned changes and fixes test or review failures,
  returning an implementation, fix, or blocked report.
- `agents/tester.md`: authors and runs tests and verifies requirements (PASS/FAIL).
- `agents/final-reviewer.md`: read-only holistic final review.

The optional third-party components are `grill-me` (including `grilling`), `handoff`, and
`teach`.

Implementation is delegated to the `developer` subagent; the main conversation
orchestrates the workflow and does not implement work itself. No installed subagent can
start subagents of its own. The read-only subagents (`discovery`, `rubber-duck`, and
`final-reviewer`) cannot use Claude Code's file-editing tools; they keep shell access for
inspection commands such as `git diff`, so their read-only role is enforced by their
instructions rather than by a sandbox.

## Development

This package is built from the
[orchestrated-codex repository](https://github.com/knuthelge/orchestrated-codex); its
installer engine and resources are generated there. See that repository's README.
