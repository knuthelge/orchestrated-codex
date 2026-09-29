"""Harness targets: every value that differs between the Codex and Claude Code installers.

The installer engine (cli, registry, sources) serves both harnesses and reads
harness-specific values only from the target activated in runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Target:
    product: str
    distribution: str
    installer_id: str
    manifest_name: str
    home_env: str
    home_default: str
    home_flag: str
    home_help: str
    # Skills live under the user's home directory for Codex and under the config home for
    # Claude Code; skills_label prefixes skill paths in install reports.
    skills_under_config_home: bool
    skills_dir: str
    skills_label: str

    def default_home(self) -> Path:
        """The config home: the target's environment variable when set, else its default."""
        return Path(os.environ.get(self.home_env, Path.home() / self.home_default))

    def skill_root(self, home: Path) -> Path:
        base = home if self.skills_under_config_home else Path.home()
        return base / self.skills_dir


CODEX = Target(
    product="Codex",
    distribution="orchestrated-codex",
    installer_id="codex-orchestrator",
    manifest_name="codex-orchestrator-install.json",
    home_env="CODEX_HOME",
    home_default=".codex",
    home_flag="--codex-home",
    home_help="home directory for agents",
    skills_under_config_home=False,
    skills_dir=".agents/skills",
    skills_label=".agents/skills",
)

CLAUDE = Target(
    product="Claude Code",
    distribution="orchestrated-claude",
    installer_id="claude-orchestrator",
    manifest_name="claude-orchestrator-install.json",
    home_env="CLAUDE_CONFIG_DIR",
    home_default=".claude",
    home_flag="--claude-config-dir",
    home_help="configuration directory for agents and skills",
    skills_under_config_home=True,
    skills_dir="skills",
    skills_label="skills",
)

TARGETS = {"codex": CODEX, "claude": CLAUDE}
