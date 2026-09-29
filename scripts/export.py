"""Export the consumer packages from the one orchestrated codebase.

Run from the repository root:

    uv run scripts/export.py                    # export every package into build/
    uv run scripts/export.py --target claude    # export one package
    uv build build/orchestrated-claude          # then build it

Each exported package is a complete, self-contained project: a pyproject.toml, this
repository's README, the installer engine from src/orchestrated, an entry point that
activates the package's target, and the resources rendered for that target from content/.
The version, dependencies, and classifiers come from the root pyproject.toml, so both
packages always share one version. Nothing exported is committed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import jinja2
import yaml

from orchestrated.targets import TARGETS

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = "content"
AGENT_TIERS = frozenset({"deep", "standard", "fast"})
ENGINE_SOURCE = "src/orchestrated"
ENGINE_FILES = ("cli.py", "registry.py", "runtime.py", "sources.py", "targets.py")
CLAUDE_EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


class RenderError(RuntimeError):
    """Raised when a source file cannot be rendered."""


@dataclass(frozen=True)
class Agent:
    stem: str
    meta: Mapping[str, object]
    body: str


@dataclass(frozen=True)
class Skill:
    stem: str
    meta: Mapping[str, object]
    body: str
    files: Mapping[str, str]


def split_frontmatter(text: str, source: Path) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        raise RenderError(f"{source}: missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise RenderError(f"{source}: unterminated frontmatter")
    meta = yaml.safe_load(text[4:end]) or {}
    if not isinstance(meta, dict):
        raise RenderError(f"{source}: frontmatter must be a mapping")
    return meta, text[end + len("\n---\n"):]


def snake(stem: str) -> str:
    return stem.replace("-", "_")


def load_agents(content_root: Path) -> list[Agent]:
    agents = []
    for path in sorted((content_root / "agents").glob("*.md")):
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"), path)
        if meta.get("tier") not in AGENT_TIERS:
            raise RenderError(f"{path}: tier must be one of {sorted(AGENT_TIERS)}")
        for key in ("description", "effort"):
            if not isinstance(meta.get(key), str):
                raise RenderError(f"{path}: {key} must be a string")
        agents.append(Agent(path.stem, meta, body))
    return agents


def load_skills(content_root: Path) -> list[Skill]:
    skills = []
    for skill_dir in sorted(path for path in (content_root / "skills").iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        meta, body = split_frontmatter(skill_md.read_text(encoding="utf-8"), skill_md)
        if not isinstance(meta.get("description"), str):
            raise RenderError(f"{skill_md}: description must be a string")
        files = {
            path.relative_to(skill_dir).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(skill_dir.rglob("*"))
            if path.is_file() and path != skill_md
        }
        skills.append(Skill(skill_dir.name, meta, body, files))
    return skills


class Renderer:
    def __init__(self, content_root: Path, target: str, vocabulary: Mapping[str, object]) -> None:
        self.content_root = content_root
        self.target = target
        self.vocabulary = vocabulary
        self.env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )
        self.env.filters["code_list"] = code_list
        self.agents = load_agents(content_root)
        self.skills = load_skills(content_root)
        skill_names = dict(vocabulary.get("skills") or {})
        self.context = {
            "target": target,
            "product": vocabulary["product"],
            "invoke": vocabulary["invoke"],
            "agent_ext": vocabulary["agent_ext"],
            "ask_user": vocabulary["ask_user"],
            "tier_models": dict(vocabulary["tiers"]),
            "agents": {snake(agent.stem): self.agent_name(agent.stem) for agent in self.agents},
            "agents_by_tier": {
                tier: [
                    self.agent_name(agent.stem)
                    for agent in self.agents
                    if agent.meta["tier"] == tier
                ]
                for tier in sorted(AGENT_TIERS)
            },
            "skills": {
                snake(skill.stem): skill_names.get(snake(skill.stem), skill.stem)
                for skill in self.skills
            },
        }

    def render(self, template: str, source: str) -> str:
        try:
            return self.env.from_string(template).render(self.context)
        except jinja2.TemplateError as error:
            raise RenderError(f"{source} ({self.target}): {error}") from error

    def agent_name(self, stem: str) -> str:
        style = self.vocabulary["agent_name_style"]
        if style == "snake":
            return snake(stem)
        if style == "kebab":
            return stem
        raise RenderError(f"{self.target}: unknown agent_name_style {style!r}")

    def agent_model(self, agent: Agent) -> str:
        overrides = agent.meta.get("model") or {}
        if not isinstance(overrides, Mapping):
            raise RenderError(f"agents/{agent.stem}.md: model must map targets to model names")
        if self.target in overrides:
            return str(overrides[self.target])
        return str(self.vocabulary["tiers"][agent.meta["tier"]])

    def render_all(self) -> dict[str, str]:
        files: dict[str, str] = {}
        agent_format = self.vocabulary["agent_format"]
        for agent in self.agents:
            source = f"agents/{agent.stem}.md"
            body = self.render(agent.body, source)
            name = f"agents/{agent.stem}{self.vocabulary['agent_ext']}"
            if agent_format == "codex":
                files[name] = self.codex_agent(agent, body, source)
            elif agent_format == "claude":
                files[name] = self.claude_agent(agent, body, source)
            else:
                raise RenderError(f"{self.target}: unknown agent_format {agent_format!r}")
        for skill in self.skills:
            name = self.context["skills"][snake(skill.stem)]
            source = f"skills/{skill.stem}/SKILL.md"
            description = self.render(str(skill.meta["description"]), source)
            skill_meta: dict[str, str | bool] = {"name": name, "description": description}
            if agent_format == "claude" and skill.meta.get("explicit_only"):
                skill_meta["disable-model-invocation"] = True
            files[f"skills/{name}/SKILL.md"] = (
                frontmatter(skill_meta, source) + self.render(skill.body, source)
            )
            if agent_format == "codex":
                metadata = self.codex_skill_metadata(skill, source)
                if metadata is not None:
                    files[f"skills/{name}/agents/openai.yaml"] = metadata
            for relative, text in skill.files.items():
                files[f"skills/{name}/{relative}"] = self.render(
                    text, f"skills/{skill.stem}/{relative}"
                )
        registry = self.content_root / "install-components.yaml.j2"
        files["install-components.yaml"] = generated_header(registry.name) + "\n" + self.render(
            registry.read_text(encoding="utf-8"), registry.name
        )
        return files

    def codex_agent(self, agent: Agent, body: str, source: str) -> str:
        if '"""' in body or "\\" in body:
            raise RenderError(f"{source}: body cannot contain triple quotes or backslashes")
        lines = [
            f"name = {basic_string(self.agent_name(agent.stem))}",
            f"description = {basic_string(self.render(str(agent.meta['description']), source))}",
            f"model = {basic_string(self.agent_model(agent))}",
            f"model_reasoning_effort = {basic_string(str(agent.meta['effort']))}",
        ]
        lines.insert(0, generated_header(source))
        if agent.meta.get("read_only"):
            lines.append('sandbox_mode = "read-only"')
        lines.append(f'developer_instructions = """\n{body}"""')
        return "\n".join(lines) + "\n"

    def claude_agent(self, agent: Agent, body: str, source: str) -> str:
        # Delegation belongs to the orchestrating main thread, so no agent may spawn its own
        # subagents; read-only agents also lose the file-editing tools.
        disallowed = ["Agent"]
        if agent.meta.get("read_only"):
            disallowed.extend(CLAUDE_EDIT_TOOLS)
        values = {
            "name": self.agent_name(agent.stem),
            "description": self.render(str(agent.meta["description"]), source),
            "model": self.agent_model(agent),
            "effort": str(agent.meta["effort"]),
            "disallowedTools": ", ".join(disallowed),
        }
        return frontmatter(values, source) + "\n" + body

    def codex_skill_metadata(self, skill: Skill, source: str) -> str | None:
        display = skill.meta.get("display")
        explicit_only = bool(skill.meta.get("explicit_only"))
        if display is None and not explicit_only:
            return None
        sections = [generated_header(source)]
        if display is not None:
            if not isinstance(display, Mapping):
                raise RenderError(f"{source}: display must be a mapping")
            lines = ["interface:"]
            for key in ("display_name", "short_description", "default_prompt"):
                if key in display:
                    lines.append(f"  {key}: {basic_string(self.render(str(display[key]), source))}")
            sections.append("\n".join(lines) + "\n")
        if explicit_only:
            sections.append("policy:\n  allow_implicit_invocation: false\n")
        return sections[0] + "\n" + "\n".join(sections[1:])


def generated_header(source: str) -> str:
    """A comment line for generated formats that allow one (TOML and YAML)."""
    return f"# Generated from content/{source} by scripts/export.py; do not edit."


def code_list(names: list[str]) -> str:
    """Join names as inline code: `a`, `b`, and `c`."""
    quoted = [f"`{name}`" for name in names]
    if len(quoted) <= 2:
        return " and ".join(quoted)
    return ", ".join(quoted[:-1]) + f", and {quoted[-1]}"


def basic_string(value: str) -> str:
    """Quote a value as a TOML basic string, which is also a YAML double-quoted scalar."""
    return json.dumps(value, ensure_ascii=False)


def frontmatter(values: Mapping[str, str | bool], source: str) -> str:
    """Emit frontmatter with plain scalars, quoting only values YAML would misread."""
    lines = []
    for key, value in values.items():
        if isinstance(value, bool):
            scalar = "true" if value else "false"
        elif yaml.safe_load(f"{key}: {value}\n") == {key: value}:
            scalar = value
        else:
            scalar = basic_string(value)
        lines.append(f"{key}: {scalar}\n")
    text = "".join(lines)
    if yaml.safe_load(text) != dict(values):
        raise RenderError(f"{source}: cannot represent frontmatter {dict(values)}")
    return f"---\n{text}---\n"


def load_targets(content_root: Path) -> dict[str, dict[str, object]]:
    path = content_root / "targets.yaml"
    targets = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(targets, dict) or not targets:
        raise RenderError(f"{path}: must map target names to vocabularies")
    return targets


def render_target(repo_root: Path, name: str) -> dict[str, str]:
    """Render one target's bundled resources, keyed by path under the resource root."""
    content_root = repo_root / CONTENT_DIR
    targets = load_targets(content_root)
    if name not in targets:
        raise RenderError(f"unknown target {name!r}; expected one of {sorted(targets)}")
    return Renderer(content_root, name, targets[name]).render_all()


def write_tree(files: Mapping[str, str], root: Path) -> None:
    for name, text in sorted(files.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def toml_list(values: Sequence[str]) -> str:
    return "[\n" + "".join(f"    {basic_string(value)},\n" for value in values) + "]"


def package_files(repo_root: Path, name: str) -> tuple[str, dict[str, str]]:
    """Return a target's distribution name and the complete source tree of its package."""
    if name not in TARGETS:
        raise RenderError(f"unknown target {name!r}; expected one of {sorted(TARGETS)}")
    target = TARGETS[name]
    package = load_targets(repo_root / CONTENT_DIR)[name]["package"]
    module = str(package["module"])
    with (repo_root / "pyproject.toml").open("rb") as file:
        root_project = tomllib.load(file)
    project = root_project["project"]
    version = str(project["version"])
    classifiers = [
        classifier for classifier in project["classifiers"]
        if not classifier.startswith("Private ::")
    ]
    source = f"src/{module}"
    files = {
        "pyproject.toml": f"""# Exported from the orchestrated codebase by scripts/export.py; do not edit.
[build-system]
requires = {toml_list(root_project["build-system"]["requires"])}
build-backend = {basic_string(root_project["build-system"]["build-backend"])}

[project]
name = {basic_string(target.distribution)}
version = {basic_string(version)}
description = {basic_string(str(package["description"]))}
readme = "README.md"
requires-python = {basic_string(project["requires-python"])}
dependencies = {toml_list(project["dependencies"])}
keywords = {toml_list([str(keyword) for keyword in package["keywords"]])}
classifiers = {toml_list(classifiers)}

[project.scripts]
{target.distribution} = "{module}.entry:main"

[tool.uv.build-backend]
module-name = "{module}"
module-root = "src"
""",
        "README.md": (repo_root / "README.md").read_text(encoding="utf-8"),
        f"{source}/__init__.py": f'''"""{target.product} orchestrated delivery installer, exported from the orchestrated codebase."""

__version__ = {basic_string(version)}
''',
        f"{source}/entry.py": f'''"""Command-line entry point for {target.distribution}."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from . import cli, runtime
from .targets import TARGETS

RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"


def main(argv: Sequence[str] | None = None) -> int:
    runtime.activate(TARGETS[{basic_string(name)}], RESOURCE_ROOT)
    return cli.main(argv)
''',
        f"{source}/__main__.py": '''from .entry import main

if __name__ == "__main__":
    raise SystemExit(main())
''',
    }
    for engine_file in ENGINE_FILES:
        files[f"{source}/{engine_file}"] = (
            repo_root / ENGINE_SOURCE / engine_file
        ).read_text(encoding="utf-8")
    for relative, text in render_target(repo_root, name).items():
        files[f"{source}/resources/{relative}"] = text
    return target.distribution, files


def export(repo_root: Path, out_root: Path, names: Sequence[str] | None = None) -> list[Path]:
    """Export the named targets' packages (all by default), replacing earlier exports."""
    exported = []
    for name in names or list(TARGETS):
        distribution, files = package_files(repo_root, name)
        destination = out_root / distribution
        if destination.exists():
            shutil.rmtree(destination)
        write_tree(files, destination)
        exported.append(destination)
    return exported


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "build", help="output directory (default: build/)"
    )
    parser.add_argument(
        "--target", action="append", choices=sorted(TARGETS),
        help="target to export; repeat for several (default: all)",
    )
    arguments = parser.parse_args(argv)
    try:
        exported = export(REPO_ROOT, arguments.out, arguments.target)
    except RenderError as error:
        print(f"export error: {error}", file=sys.stderr)
        return 2
    for path in exported:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
