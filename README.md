# Orchestrated delivery for Codex and Claude Code

A collection of software delivery skills and focused subagents for **Codex** and
**Claude Code**. It installs a reusable orchestration workflow, an explicit code-review
workflow, and custom subagents so the coding agent follows a structured process for
discovery, planning, independent plan review, implementation, testing, and final review.

The delivery skill instructs the main conversation to orchestrate only: classify the
request, delegate bounded work when specialization, independent review, or parallelism
materially improves the result, and verify the result. The orchestrator hands each subagent
a scoped digest of the plan and a discovery impact map, so work proceeds without re-reading
whole artifacts, keeping runs fast and cheap under the same phased contract.

The same workflow ships as one package per harness, built from this repository and
released together with the same version:

| Harness | Package and command | Invoke a skill with |
|---|---|---|
| Codex | [`orchestrated-codex`](https://pypi.org/project/orchestrated-codex/) | `$orchestrated-delivery` |
| Claude Code | [`orchestrated-claude`](https://pypi.org/project/orchestrated-claude/) | `/orchestrated-delivery` |

Both packages offer the same components, command-line options, and installer guarantees;
only the file formats and install locations differ.

## Installation

Install with [uv](https://docs.astral.sh/uv/), using the package for your harness:

```sh
uvx orchestrated-codex --install
uvx orchestrated-claude --install
```

The installer opens a guided checklist. Use the arrow keys to move, Space to select one or
more components, and Enter to install the selected set. Previously installed components are
preselected; deselecting one removes its unchanged files while preserving locally modified
files.

To run from source instead, use a checkout and pick the harness with `--codex` (the default)
or `--claude`:

```sh
git clone https://github.com/knuthelge/orchestrated-codex.git
cd orchestrated-codex
uv run main.py --codex --install
uv run main.py --claude --install
```

For CI or other unattended installs, install the bundled set or name a comma-separated
component set. The options are the same for both packages:

```sh
uvx orchestrated-claude --install --all
uvx orchestrated-codex --install --components code-review
uvx orchestrated-claude --install --components handoff
uvx orchestrated-codex --install --components grill-me,teach
uvx orchestrated-claude --install --all --include-third-party
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
selection. Its MIT licence label is metadata supplied by this project's
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

### Where files are installed

| | Codex | Claude Code |
|---|---|---|
| Skills | `$HOME/.agents/skills` | `<config>/skills` |
| Subagents | `<codex home>/agents/*.toml` | `<config>/agents/*.md` |
| Installation manifest | `<codex home>/codex-orchestrator-install.json` | `<config>/claude-orchestrator-install.json` |
| Home directory | `CODEX_HOME` if set, else `~/.codex` | `CLAUDE_CONFIG_DIR` if set, else `~/.claude` |
| Override flag | `--codex-home PATH` (skills stay under `$HOME/.agents/skills`) | `--claude-config-dir PATH` (skills and subagents move together) |

## Usage

Start a new conversation after installation (for Codex, restart it). Both workflows run
only when you invoke them; neither harness selects them automatically:

```text
$orchestrated-delivery implement this feature          # Codex
$orchestrated-code-review review the current changes   # Codex

/orchestrated-delivery implement this feature          # Claude Code
/orchestrated-code-review review the current changes   # Claude Code
```

The code-review skill writes its independently verified, prioritized findings to
`code-review.md`. It reviews local changes by default and uses pull-request context when
available. Every review also tracks the mandatory ten-stage workflow and its per-finding
validation in `.agent-work/code-review-checklist.md`. The review does not publish comments
or modify code unless you separately ask for that action. In Claude Code, the bundled
`/code-review` skill is unaffected.

## Uninstall

Remove the installed files with the package you installed:

```sh
uvx orchestrated-codex --uninstall
uvx orchestrated-claude --uninstall
```

The installer refuses to overwrite files it does not own. Uninstall removes only installed
files that still match the recorded hashes and then deletes the manifest; locally modified
installed files are preserved and reported.

## Installed components

The bundled components are the same for both harnesses:

- `orchestrated-delivery` (skill): task classification and an adaptive discovery, planning,
  independent plan review, implementation, testing, and final-review workflow.
- `orchestrated-code-review` (skill, component `code-review`): explicit-only review of a
  local diff or pull request, producing a comprehensive, prioritized, independently verified
  `code-review.md` report without a minimum or maximum finding count.
- Seven subagents, installed with `orchestrated-delivery`:

| Subagent | Role | Codex model | Claude Code model |
|---|---|---|---|
| `discovery` | Read-only codebase reconnaissance | `gpt-6-luna` | `haiku` |
| `spec-designer` | Requirements and technical design | `gpt-6-sol` | `opus` |
| `rubber-duck` | Independent PRD peer review (PASS/CONCERNS) | `gpt-6-sol` | `opus` |
| `ui-designer` | Visual design specification for substantial UI work | `gpt-6-sol` | `opus` |
| `developer` | Implements planned changes and fixes test or review failures | `gpt-5.6-terra` | `sonnet` |
| `tester` | Authors and runs tests and verifies requirements (PASS/FAIL) | `gpt-5.6-terra` | `sonnet` |
| `final-reviewer` | Read-only holistic final review | `gpt-6-sol` | `opus` |

Codex names these agents with underscores (`rubber_duck`, `final_reviewer`); Claude Code
uses the hyphenated names shown.

The optional third-party components are `grill-me` (including `grilling`), `handoff`,
and `teach`. They install under the skills directory when selected.

Implementation is delegated to the `developer` subagent. The main conversation
orchestrates the workflow and does not implement work itself. Independent delegations use
fresh context and the orchestrator does not poll while waiting for subagent results.
Read-only subagents are restricted by each harness: Codex runs them in a read-only sandbox,
and Claude Code removes its file-editing tools from them (they keep shell access for
inspection commands such as `git diff`). In Claude Code, no installed subagent can start
subagents of its own.

### Upgrading from earlier Codex versions

The review skill was previously named `code-review` and invoked as `$code-review`. Updating
an existing installation removes the old `code-review` skill directory and installs
`orchestrated-code-review`; if you edited the old skill, your copy is preserved and reported,
and remains available until you delete it. The component is still selected with
`--components code-review`. Implementation, previously delegated to the built-in `worker`,
now goes to the installed `developer` agent.

## Development

Run the tests from a checkout:

```sh
uv run python -m unittest discover -s tests
```

The repository builds both distributions from one source. `orchestrated-codex` is the
project at the repository root; `orchestrated-claude` is a uv workspace member in
`packages/orchestrated-claude`.

Skills, agents, and the component registry are authored once under `content/` and
rendered into each harness's native format:

- `src/codex_orchestrator/resources/`: Codex agent TOML files, skills, and `openai.yaml`
  skill metadata.
- `packages/orchestrated-claude/src/claude_orchestrator/resources/`: Claude Code agent
  Markdown files and skills.

Both resource directories are generated: edit `content/`, never the generated files.
`content/targets.yaml` holds each harness's vocabulary (model tiers, agent naming,
invocation prefix, ask-the-user wording); sources reference it through Jinja placeholders
such as `{{ agents.tester }}`, `{{ product }}`, and `{{ invoke }}{{ skills.code_review }}`.
Use a `{% if target == "codex" %}` block only where the harnesses behave differently, not
merely where they name things differently. Agent sources are Markdown with `description`,
`tier` (`deep`, `standard`, or `fast`), `effort`, and `read_only` frontmatter and the
instructions as the body; an optional `model:` mapping overrides the tier model per target.

The installer engine (`cli.py`, `registry.py`, `sources.py`, `target.py`, `__main__.py`) is
authored in `src/codex_orchestrator/` and copied verbatim into the Claude package by the
renderer, as is this README. Everything that differs between harnesses lives in
`target.py`; each package's hand-written `_active.py` selects its target. Re-render after
editing sources, the engine, or this README, and commit the result; the test suite and the
release workflow fail when committed output is stale:

```sh
uv run scripts/render_resources.py          # regenerate
uv run scripts/render_resources.py --check  # verify only
```

Installable choices are defined in `content/install-components.yaml.j2`, rendered to each
package's `resources/install-components.yaml`. Its schema-v2 component entries supply the
checklist text, `active` state, optional dependencies, destination roots (`config_home` or
`skills`), relative destinations, and resource sources. A source is either `kind: bundled`
with a path relative to packaged resources, or `kind: github` with a named repository
catalog entry and a path relative to that repository. The `repositories` catalog holds each
trusted GitHub owner and repository, tracked ref, display name, and licence metadata. The
chooser derives its GitHub homepage from the owner and repository. The shipped
`mattpocock-skills` entry uses `mattpocock/skills` on `main`; selected third-party skills
refresh on each install.

To add a trusted third-party source, add its public GitHub repository to the catalog,
then give a component one `github` resource per required file, with its
repository-relative path and installed destination. Include `SKILL.md` for every
skill directory and list any companion skill's files on the same component; wrap files that
only one harness uses, such as Codex `agents/openai.yaml` metadata, in a target block. New
upstream files require a registry update, while existing listed files refresh from
the tracked branch on a selected install. No repository-specific installer branch
is needed. Review the upstream content and licence metadata before shipping the
registry change. Registry loading rejects unknown fields, malformed repository IDs, unsafe
paths, and duplicate source or destination mappings. For a selected third-party source,
installation resolves the configured ref to an exact commit and fetches each listed file
directly at that commit. It validates all requested files and destination shapes before
changing installed files or the manifest. A missing listed file or `SKILL.md` fails that
preflight.

Inactive components that were never installed are hidden and excluded from unattended
installs. If an installed component becomes inactive, the guided installer presents it as
retired so the user can retain it or deselect it for safe removal. Bundled source paths
must remain relative to packaged resources; GitHub source paths must remain relative to
their cataloged repository. All destinations must remain under a supported installation
root, and no file may be installed inside a directory that another resource installs as a
whole.

Build the wheels and source distributions and inspect them:

```sh
uv build --out-dir dist/codex
uv build --out-dir dist/claude packages/orchestrated-claude
```

The Codex distribution and command are named `orchestrated-codex`, with the importable
module `codex_orchestrator`. The Claude Code distribution and command are named
`orchestrated-claude`, with module `claude_orchestrator`.

Both packages share one version. Update `version` in both `pyproject.toml` files and
`__version__` in both packages together; a `vX.Y.Z` tag builds, tests, and smoke-tests both
distributions and publishes both to PyPI. Running the publish workflow manually performs the
same checks, builds, and smoke tests without publishing.
