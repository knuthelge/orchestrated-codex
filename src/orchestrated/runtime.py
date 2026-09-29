"""The harness and bundled resources the installer runs with.

The installer engine serves every harness. Each entry point activates one target and the
resource directory rendered for it before calling the engine: an exported package's entry
module does so with its bundled resources, and the source-checkout launcher does so with
resources rendered from content/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .targets import Target


@dataclass(frozen=True)
class Runtime:
    target: Target
    resource_root: Path


_current: Runtime | None = None


def activate(target: Target, resource_root: Path) -> None:
    global _current
    _current = Runtime(target, resource_root)


def current() -> Runtime:
    if _current is None:
        raise RuntimeError("No installer target is active; call runtime.activate() first")
    return _current
