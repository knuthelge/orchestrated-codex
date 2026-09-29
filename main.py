"""Run the installer for either harness straight from a source checkout.

    uv run main.py --codex --install ...
    uv run main.py --claude --install ...

The launcher renders the chosen target's resources from content/ into a temporary
directory, activates that target, and runs the installer engine from src/orchestrated.
All arguments other than the target flag pass through to the installer.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export  # noqa: E402  (scripts/export.py)
from orchestrated import cli, runtime  # noqa: E402
from orchestrated.targets import TARGETS  # noqa: E402

TARGET_FLAGS = {f"--{name}": name for name in TARGETS}


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    selected = [argument for argument in arguments if argument in TARGET_FLAGS]
    if len(selected) != 1:
        flags = " or ".join(TARGET_FLAGS)
        print(f"main.py: error: choose exactly one target: {flags}", file=sys.stderr)
        return 2
    remaining = [argument for argument in arguments if argument not in TARGET_FLAGS]
    name = TARGET_FLAGS[selected[0]]
    with tempfile.TemporaryDirectory(prefix=f"orchestrated-{name}-resources-") as temporary:
        resource_root = Path(temporary)
        export.write_tree(export.render_target(REPO_ROOT, name), resource_root)
        runtime.activate(TARGETS[name], resource_root)
        return cli.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
