# orchestrated-codex

A standalone, Codex-native software delivery workflow. It installs a reusable
orchestration skill and focused custom agents so Codex follows a structured process
for discovery, planning, independent plan review, implementation, testing, and final
review. The skill instructs the primary Codex thread to orchestrate only — classify the
request, delegate every unit of work to a named subagent or the built-in `worker`, and
verify the result. The orchestrator hands each subagent a scoped digest of the plan and a
discovery impact map so work proceeds without re-reading whole artifacts, keeping runs fast
and cheap under the same phased contract.

## Installation

Install with [uv](https://docs.astral.sh/uv/):

```sh
uvx orchestrated-codex --install
```

To run from source instead, use a checkout:

```sh
git clone https://github.com/knuthelge/orchestrated-codex.git
cd orchestrated-codex
uv run main.py --install
```

Restart Codex or start a new conversation after installation. To use the workflow,
invoke it explicitly with `$orchestrated-delivery`; Codex will not select it
automatically based on the request.

The installer writes to two independent roots:

- The **skill** installs under `$HOME/.agents/skills/orchestrated-delivery`, a documented
  Codex skills root, so Codex discovers it.
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

- `orchestrated-delivery` (skill): task classification and an adaptive discovery, planning,
  independent plan review, implementation, testing, and final-review workflow. Installs under
  `$HOME/.agents/skills`.
- `agents/discovery.toml`: read-only codebase reconnaissance.
- `agents/spec-designer.toml`: requirements and technical design.
- `agents/rubber-duck.toml`: independent PRD peer review (PASS/CONCERNS).
- `agents/ui-designer.toml`: visual design specification for substantial UI work.
- `agents/tester.toml`: authors and runs tests and verifies requirements (PASS/FAIL).
- `agents/final-reviewer.toml`: read-only holistic final review.

Implementation is delegated to Codex's built-in `worker`. The primary Codex thread
orchestrates the workflow and does not implement work itself.

## Development

Run from a checkout and execute the tests:

```sh
uv run main.py --install --codex-home /path/to/test-home
uv run python -m unittest discover -s tests
```

Build the wheel and source distribution and inspect them:

```sh
uv build
```

The PyPI distribution and command are both named `orchestrated-codex`. The importable
Python module remains `codex_orchestrator` for compatibility.
