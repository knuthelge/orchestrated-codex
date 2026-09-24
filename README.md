# orchestrated-codex

A standalone collection of Codex-native software delivery skills. It installs a reusable
orchestration workflow, an explicit code-review workflow, and focused custom agents so
Codex can follow a structured process for discovery, planning, independent plan review,
implementation, testing, and final review. The delivery skill instructs the primary Codex
thread to orchestrate only — classify the
request, delegate bounded work when specialization, independent review, or parallelism
materially improves the result, and verify the result. The orchestrator hands each
subagent a scoped digest of the plan and a
discovery impact map so work proceeds without re-reading whole artifacts, keeping runs fast
and cheap under the same phased contract.

## Installation

Install with [uv](https://docs.astral.sh/uv/):

```sh
uvx orchestrated-codex --install
```

The installer opens a guided checklist. Use the arrow keys to move, Space to select one or
more components, and Enter to install the selected set. Previously installed components are
preselected; deselecting one removes its unchanged files while preserving locally modified
files.

To run from source instead, use a checkout:

```sh
git clone https://github.com/knuthelge/orchestrated-codex.git
cd orchestrated-codex
uv run main.py --install
```

For CI or other unattended installs, install the bundled set or name a comma-separated
component set:

```sh
uvx orchestrated-codex --install --all
uvx orchestrated-codex --install --components code-review
uvx orchestrated-codex --install --components handoff
uvx orchestrated-codex --install --components grill-me,teach
uvx orchestrated-codex --install --all --include-third-party
```

Named component installs automatically include dependencies declared by the component
registry. `--install --all` updates bundled components and makes no network request;
it leaves any previously installed third-party components in place without refreshing them.
Use `--components` to select specific third-party skills, or add
`--include-third-party` to `--all` to install or refresh every active third-party component.
A plain `--install` requires an interactive terminal.

### Optional third-party skills

The checklist also offers `grill-me`, `handoff`, and `teach` from
[Matt Pocock's skills repository](https://github.com/mattpocock/skills). These are optional
and are copied from `skills/productivity/` in that repository when selected. Selecting
`grill-me` also installs its required `grilling` companion skill; `grilling` is not a
separate choice. The checklist shows the publisher, homepage, and licence before
selection. Its MIT licence label is metadata supplied by this package's
registry, not a fresh verification of upstream licensing.

These choices track the repository's `main` branch. Selecting one trusts future
upstream content from that branch. A selected skill is fetched when you run an install
that includes it, so running the same explicit command later updates it to the latest
available commit without a package release. One checklist selection starts the
download, validation, and installation; there is no second confirmation or commit
comparison to perform. The installation manifest records the exact resolved commit
SHA and source path for every installed file so its origin can be audited later.

To remove a third-party skill, deselect it in the checklist. The installer removes
its files only when they still match the recorded hashes and preserves locally
modified files. Deselecting `grill-me` applies to both `grill-me` and `grilling`.
Ordinary `--install --all` leaves installed third-party files, ownership, provenance,
and selections unchanged, with no GitHub request. Deselecting and uninstalling use
the local manifest and require no network connection. An explicit third-party install
needs GitHub access; if source retrieval or validation fails before installation,
installed files and the manifest remain unchanged, including any bundled components
selected in that run.

The installer makes one unauthenticated GitHub API request to resolve the branch,
then downloads only the listed files from GitHub's raw-content host at that commit.
GitHub may rate-limit either service. If it reports a reset time, the installer
shows that time in the local time zone; it does not retry automatically.

Restart Codex or start a new conversation after installation. Invoke either workflow
explicitly; Codex will not select them automatically based on the request:

```text
$orchestrated-delivery implement this feature
$code-review review the current changes
```

`$code-review` writes its independently verified, prioritized findings to
`code-review.md`. It reviews local changes by default and uses pull-request context when
available. Every review also tracks the mandatory ten-stage workflow and its per-finding
validation in `.agent-work/code-review-checklist.md`. The review does not publish comments
or modify code unless you separately ask for that action.

The installer writes to two independent roots:

- The **skills** install under `$HOME/.agents/skills`, a documented Codex skills root, so
  Codex discovers them.
- The **agents** install under the Codex home — `CODEX_HOME` when it is set and `~/.codex`
  otherwise — as `agents/*.toml`.

Use `--codex-home PATH` to target another Codex home for the agents; the skill always
resolves under `$HOME/.agents/skills`.

## Uninstall

Remove the installed files with:

```sh
uvx orchestrated-codex --uninstall
```

The installer refuses to overwrite files it does not own. Uninstall removes only installed
files that still match the recorded hashes across both roots and then deletes the manifest;
locally modified installed files are preserved and reported.

## Installed components

The bundled components are:

- `orchestrated-delivery` (skill): task classification and an adaptive discovery, planning,
  independent plan review, implementation, testing, and final-review workflow. Installs under
  `$HOME/.agents/skills`.
- `code-review` (skill): explicit-only review of a local diff or pull request, producing a
  comprehensive, prioritized, independently verified `code-review.md` report without a
  minimum or maximum finding count.
- `agents/discovery.toml`: read-only codebase reconnaissance.
- `agents/spec-designer.toml`: requirements and technical design.
- `agents/rubber-duck.toml`: independent PRD peer review (PASS/CONCERNS).
- `agents/ui-designer.toml`: visual design specification for substantial UI work.
- `agents/tester.toml`: authors and runs tests and verifies requirements (PASS/FAIL).
- `agents/final-reviewer.toml`: read-only holistic final review.

The optional third-party components are `grill-me` (including `grilling`), `handoff`,
and `teach`. They install under `$HOME/.agents/skills` when selected.

Implementation is delegated to Codex's built-in `worker`. The primary Codex thread
orchestrates the workflow and does not implement work itself. Independent delegations use
fresh context by default and the orchestrator avoids short polling loops while waiting for
subagent results.

## Development

Run from a checkout and execute the tests:

```sh
uv run main.py --install --all --codex-home /path/to/test-home
uv run python -m unittest discover -s tests
```

Installable choices are defined in
`src/codex_orchestrator/resources/install-components.yaml`. Its schema-v2 component entries
supply the checklist text, `active` state, optional dependencies, destination roots, relative
destinations, and resource sources. A source is either `kind: bundled` with a path
relative to packaged resources, or `kind: github` with a named repository catalog
entry and a path relative to that repository. The `repositories` catalog holds each
trusted GitHub owner and repository, tracked ref, display name, and licence
metadata. The chooser derives its GitHub homepage from the owner and repository.
The shipped `mattpocock-skills` entry uses `mattpocock/skills` on `main`;
selected third-party skills refresh on each install.

To add a trusted third-party source, add its public GitHub repository to the catalog,
then give a component one `github` resource per required file, with its
repository-relative path and installed destination. Include `SKILL.md` for every
skill directory and list any companion skill's files on the same component. New
upstream files require a registry update, while existing listed files refresh from
the tracked branch on a selected install. No repository-specific installer branch
is needed. Review the upstream content and licence metadata before shipping the
registry change. Registry loading rejects
unknown fields, malformed repository IDs, unsafe paths, and duplicate source or
destination mappings. For a selected third-party source, installation resolves
the configured ref to an exact commit
and fetches each listed file directly at that commit. It validates all requested
files and destination shapes before changing installed files or the manifest.
A missing listed file or `SKILL.md` fails that preflight.

Inactive components that were never installed are hidden and excluded from unattended
installs. If an installed component becomes inactive, the guided installer presents it as
retired so the user can retain it or deselect it for safe removal. Bundled source paths
must remain relative to packaged resources; GitHub source paths must remain relative to
their cataloged repository. All destinations must remain under a supported installation
root.

Build the wheel and source distribution and inspect them:

```sh
uv build
```

The PyPI distribution and command are both named `orchestrated-codex`. The importable
Python module remains `codex_orchestrator` for compatibility.
