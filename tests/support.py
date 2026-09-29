"""Shared test support: the exporter, and activating a target with its rendered resources.

Tests run against the one codebase. A test class names its target, and setUp activates that
target with resources rendered from content/ (once per test process).
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import export  # noqa: E402  (scripts/export.py)
from orchestrated import runtime  # noqa: E402
from orchestrated.targets import TARGETS  # noqa: E402

_RENDERED_ROOT = Path(tempfile.mkdtemp(prefix="orchestrated-test-resources-"))
atexit.register(shutil.rmtree, _RENDERED_ROOT, ignore_errors=True)


def resource_root(name: str) -> Path:
    """The resources rendered for a target, rendered on first use."""
    root = _RENDERED_ROOT / name
    if not root.exists():
        export.write_tree(export.render_target(REPO_ROOT, name), root)
    return root


def activate(name: str) -> Path:
    root = resource_root(name)
    runtime.activate(TARGETS[name], root)
    return root


class TargetMixin:
    """Mixin for test classes run once per target; subclasses set target_name."""

    target_name = ""

    def setUp(self) -> None:
        super().setUp()
        self.resources = activate(self.target_name)
        self.target = TARGETS[self.target_name]
        self.vocabulary = export.load_targets(REPO_ROOT / "content")[self.target_name]
        self.agent_ext = str(self.vocabulary["agent_ext"])
        # Codex skills and third-party skills carry agents/openai.yaml metadata.
        self.skill_metadata = self.vocabulary["agent_format"] == "codex"

    def expected_skill_root(self, home: Path, config_home: Path) -> Path:
        """Where the target installs skills, given $HOME and its config home."""
        base = config_home if self.target.skills_under_config_home else home
        return base / self.target.skills_dir
