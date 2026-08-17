# CodexOrchestrator

A standalone, Codex-native software delivery workflow. It installs a reusable
orchestration skill and focused custom agents into your Codex home directory,
giving Codex a structured process for discovery, planning, implementation,
verification, and final review.

## Install with uv

Install the command in an isolated environment:

```sh
uv tool install codex-orchestrator
codex-orchestrator --install
```

Restart Codex or start a new conversation after installation. Invoke the
workflow explicitly with `$orchestrated-delivery`, or let Codex select it when a
request matches its description.

Upgrade the tool and then update the installed Codex files:

```sh
uv tool upgrade codex-orchestrator
codex-orchestrator --install
```

By default, files are installed under `CODEX_HOME` when it is set and under
`~/.codex` otherwise. Use `--codex-home PATH` to target another Codex home.

## Uninstall

First remove the files copied into the Codex home, then remove the uv-managed
command:

```sh
codex-orchestrator --uninstall
uv tool uninstall codex-orchestrator
```

The installer refuses to overwrite files it does not own. Uninstall removes
only installed files that still match the recorded hashes; locally modified
installed files are preserved and reported.

## Installed components

- `skills/orchestrated-delivery`: task classification and an adaptive discovery,
  planning, implementation, verification, and review workflow.
- `agents/discovery.toml`: read-only codebase reconnaissance.
- `agents/spec-designer.toml`: requirements and technical design.
- `agents/ui-designer.toml`: visual design specification for substantial UI work.
- `agents/verifier.toml`: tests, runtime checks, and requirement verification.
- `agents/final-reviewer.toml`: read-only holistic final review.

Custom agents are deliberately narrow. The primary Codex agent or built-in
worker performs implementation and coordinates the workflow.

## Development and publishing

Run from a checkout and execute the tests:

```sh
uv run main.py --install --codex-home /path/to/test-home
uv run python -m unittest discover -s tests
```

Build the wheel and source distribution, inspect them, and publish after setting
the release version and adding the project owner's chosen license and publisher
metadata:

```sh
uv build
uv publish
```

The distribution and installed command are both named `codex-orchestrator`.
