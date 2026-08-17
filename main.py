"""Compatibility launcher for running the installer from a source checkout."""

from codex_orchestrator.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
