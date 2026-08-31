# CodexOrchestrator

A standalone, Codex-native software delivery workflow. It installs a reusable
orchestration skill and focused custom agents so Codex follows a structured process
for discovery, planning, independent plan review, implementation, testing, and final
review. The skill instructs the primary Codex thread to orchestrate only — classify the
request, delegate every unit of work to a named subagent or the built-in `worker`, and
verify the result. The orchestrator hands each subagent a scoped digest of the plan and a
discovery impact map so work proceeds without re-reading whole artifacts, keeping runs fast
and cheap under the same phased contract.

## Install from source

The package is not published to a package index. Install it from a checkout with
[uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/knuthelge/CodexOrchestrator.git
cd CodexOrchestrator
uv tool install .
codex-orchestrator --install
```

To install a specific revision without cloning, point uv at a git reference:

```sh
uv tool install "git+https://github.com/knuthelge/CodexOrchestrator.git"
codex-orchestrator --install
```

Restart Codex or start a new conversation after installation. Invoke the workflow
explicitly with `$orchestrated-delivery`, or let Codex select it when a request matches
its description.

The installer writes to two independent roots:

- The **skill** installs under `$HOME/.agents/skills/orchestrated-delivery`, a documented
  Codex skills root, so Codex discovers it.
- The **agents** install under the Codex home — `CODEX_HOME` when it is set and `~/.codex`
  otherwise — as `agents/*.toml`.

Use `--codex-home PATH` to target another Codex home for the agents; the skill always
resolves under `$HOME/.agents/skills`.

## Uninstall

First remove the files copied into the two roots, then remove the uv-managed command:

```sh
codex-orchestrator --uninstall
uv tool uninstall codex-orchestrator
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

The distribution and installed command are both named `codex-orchestrator`.
