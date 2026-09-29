"""Launcher for running either installer from a source checkout.

    uv run main.py [--codex | --claude] --install ...

--codex (the default) runs the orchestrated-codex installer; --claude runs the
orchestrated-claude installer. All other arguments pass through unchanged.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

TARGET_FLAGS = ("--codex", "--claude")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    selected = [argument for argument in arguments if argument in TARGET_FLAGS]
    if len(selected) > 1:
        print("main.py: error: use only one of --codex and --claude", file=sys.stderr)
        return 2
    remaining = [argument for argument in arguments if argument not in TARGET_FLAGS]
    if selected == ["--claude"]:
        from claude_orchestrator.cli import main as run
    else:
        from codex_orchestrator.cli import main as run
    return run(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
