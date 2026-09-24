"""Load and validate the install component registry."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.events import AliasEvent

SCHEMA_VERSION = 2
INSTALL_ROOTS = frozenset({"codex_home", "skills"})


class RegistryError(RuntimeError):
    """The component registry is invalid."""


class _RegistryLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects aliases in the packaged registry."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise RegistryError(
                f"YAML aliases are not allowed (line {event.start_mark.line + 1})"
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise RegistryError(f"duplicate YAML field {key!r} (line {key_node.start_mark.line + 1})")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class InstallResource:
    source: Path | BundledSource | GitHubSource
    install_root: str
    destination: Path


@dataclass(frozen=True)
class BundledSource:
    kind: str
    path: Path


@dataclass(frozen=True)
class GitHubSource:
    kind: str
    repository: str
    path: Path


@dataclass(frozen=True)
class GitHubRepository:
    id: str
    owner: str
    repository: str
    ref: str
    display_name: str
    licence: str

    @property
    def homepage(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}"


@dataclass(frozen=True)
class InstallComponent:
    id: str
    title: str
    active: bool
    description: str
    includes: str
    depends_on: tuple[str, ...]
    resources: tuple[InstallResource, ...]


@dataclass(frozen=True)
class ComponentRegistry:
    components: tuple[InstallComponent, ...]
    resource_root: Path
    repositories: Mapping[str, GitHubRepository] | None = None

    @property
    def by_id(self) -> dict[str, InstallComponent]:
        return {component.id: component for component in self.components}


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise RegistryError(f"{context} must be a mapping")
    return value


def _only_keys(
    value: Mapping[str, object], expected: set[str], context: str,
    *, optional: Collection[str] = (),
) -> None:
    unknown = set(value) - expected - set(optional)
    missing = expected - set(value)
    if unknown:
        raise RegistryError(
            f"{context} has unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise RegistryError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{context} must be a non-empty string")
    return value.strip()


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"{context} must be true or false")
    return value


def _relative_path(value: object, context: str) -> Path:
    text = _text(value, context)
    candidate = PurePosixPath(text)
    if (
        candidate.is_absolute()
        or candidate == PurePosixPath(".")
        or ".." in candidate.parts
        or any(char in text for char in ("\\", ":", "\x00"))
        or any(ord(char) < 32 or ord(char) == 127 for char in text)
        or any(part in ("", ".", "..") for part in text.split("/"))
    ):
        raise RegistryError(f"{context} must be a safe relative path: {text!r}")
    return Path(*candidate.parts)


def _component_id(value: object, context: str) -> str:
    result = _text(value, context)
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", result) is None:
        raise RegistryError(
            f"{context} must contain only lowercase letters, digits, and hyphens"
        )
    return result


def _repository_catalog(value: object) -> dict[str, GitHubRepository]:
    catalog = _mapping(value, "registry.repositories")
    result: dict[str, GitHubRepository] = {}
    for raw_id, raw_repo in catalog.items():
        repo_id = _component_id(raw_id, "registry.repositories key")
        context = f"registry.repositories.{repo_id}"
        data = _mapping(raw_repo, context)
        _only_keys(data, {"owner", "repository", "ref", "display_name", "licence"}, context)
        owner = _text(data["owner"], f"{context}.owner")
        repository = _text(data["repository"], f"{context}.repository")
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
            raise RegistryError(f"{context}.owner is not a valid GitHub owner")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", repository) or repository.endswith(".git"):
            raise RegistryError(f"{context}.repository is not a valid GitHub repository")
        ref = _text(data["ref"], f"{context}.ref")
        ref_segments = ref.split("/")
        if (
            ref != data["ref"]
            or ".." in ref
            or any(
                not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", segment)
                or segment.endswith((".", ".lock"))
                for segment in ref_segments
            )
        ):
            raise RegistryError(f"{context}.ref is not a safe Git ref")
        result[repo_id] = GitHubRepository(
            id=repo_id, owner=owner, repository=repository, ref=ref,
            display_name=_text(data["display_name"], f"{context}.display_name"),
            licence=_text(data["licence"], f"{context}.licence"),
        )
    return result


def _string_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RegistryError(f"{context} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{context}[{index}]"))
    if len(result) != len(set(result)):
        raise RegistryError(f"{context} contains duplicates")
    return tuple(result)


def _load_resource(
    value: object, context: str, resource_root: Path,
    repositories: Mapping[str, GitHubRepository], schema_version: int,
) -> InstallResource:
    data = _mapping(value, context)
    _only_keys(data, {"source", "install_root", "destination"}, context)
    if schema_version == 1:
        source: Path | BundledSource | GitHubSource = _relative_path(data["source"], f"{context}.source")
        bundled_path = source
    else:
        source_data = _mapping(data["source"], f"{context}.source")
        kind = _text(source_data.get("kind"), f"{context}.source.kind")
        if kind == "bundled":
            _only_keys(source_data, {"kind", "path"}, f"{context}.source")
            bundled_path = _relative_path(source_data["path"], f"{context}.source.path")
            source = BundledSource(kind="bundled", path=bundled_path)
        elif kind == "github":
            _only_keys(source_data, {"kind", "repository", "path"}, f"{context}.source")
            repository_id = _component_id(source_data["repository"], f"{context}.source.repository")
            if repository_id not in repositories:
                raise RegistryError(f"{context}.source references unknown repository {repository_id!r}")
            source = GitHubSource(
                kind="github", repository=repository_id,
                path=_relative_path(source_data["path"], f"{context}.source.path"),
            )
            bundled_path = None
        else:
            raise RegistryError(f"{context}.source.kind must be bundled or github")
    destination = _relative_path(data["destination"], f"{context}.destination")
    install_root = _text(data["install_root"], f"{context}.install_root")
    if install_root not in INSTALL_ROOTS:
        raise RegistryError(
            f"{context}.install_root must be one of: {', '.join(sorted(INSTALL_ROOTS))}"
        )
    if bundled_path is not None:
        source_path = resource_root / bundled_path
        if not source_path.exists():
            raise RegistryError(f"{context}.source does not exist: {bundled_path.as_posix()}")
        if not source_path.resolve().is_relative_to(resource_root.resolve()):
            raise RegistryError(
                f"{context}.source escapes the resource root: {bundled_path.as_posix()}"
            )
    return InstallResource(source, install_root, destination)


def _validate_dependency_graph(components: Sequence[InstallComponent]) -> None:
    by_id = {component.id: component for component in components}
    state: dict[str, int] = {}
    trail: list[str] = []

    def visit(component_id: str) -> None:
        if state.get(component_id) == 2:
            return
        if state.get(component_id) == 1:
            cycle_start = trail.index(component_id)
            cycle = trail[cycle_start:] + [component_id]
            raise RegistryError(f"component dependency cycle: {' -> '.join(cycle)}")
        state[component_id] = 1
        trail.append(component_id)
        for dependency in by_id[component_id].depends_on:
            if dependency not in by_id:
                raise RegistryError(
                    f"component {component_id!r} has unknown dependency {dependency!r}"
                )
            visit(dependency)
        trail.pop()
        state[component_id] = 2

    for component in components:
        visit(component.id)


def load_registry(path: Path, resource_root: Path) -> ComponentRegistry:
    """Read and strictly validate a YAML component registry."""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_RegistryLoader)
    except (OSError, yaml.YAMLError) as error:
        raise RegistryError(
            f"Cannot read component registry {path}: {error}"
        ) from error
    data = _mapping(raw, "registry")
    schema_version = data.get("schema_version")
    if schema_version not in (1, SCHEMA_VERSION) or isinstance(schema_version, bool):
        raise RegistryError(
            f"unsupported registry schema version {schema_version!r}; "
            f"expected 1 or {SCHEMA_VERSION}"
        )
    if schema_version == 1:
        _only_keys(data, {"schema_version", "components"}, "registry")
        repositories: dict[str, GitHubRepository] = {}
    else:
        _only_keys(data, {"schema_version", "repositories", "components"}, "registry")
        repositories = _repository_catalog(data["repositories"])
    raw_components = data["components"]
    if not isinstance(raw_components, list) or not raw_components:
        raise RegistryError("registry.components must be a non-empty list")

    components: list[InstallComponent] = []
    seen_ids: set[str] = set()
    destinations: dict[tuple[str, Path], str] = {}
    sources: dict[Path | BundledSource | GitHubSource, str] = {}
    component_fields = {
        "id",
        "title",
        "active",
        "description",
        "includes",
        "resources",
    }
    for index, raw_component in enumerate(raw_components):
        context = f"registry.components[{index}]"
        component_data = _mapping(raw_component, context)
        _only_keys(component_data, component_fields, context, optional={"depends_on"})
        component_id = _component_id(component_data["id"], f"{context}.id")
        if component_id in seen_ids:
            raise RegistryError(f"duplicate component id: {component_id}")
        seen_ids.add(component_id)
        raw_resources = component_data["resources"]
        if not isinstance(raw_resources, list) or not raw_resources:
            raise RegistryError(f"{context}.resources must be a non-empty list")
        resources = tuple(
            _load_resource(
                resource, f"{context}.resources[{resource_index}]", resource_root,
                repositories, schema_version,
            )
            for resource_index, resource in enumerate(raw_resources)
        )
        for resource in resources:
            source_key = resource.source
            if source_key in sources:
                raise RegistryError(
                    f"duplicate resource source {source_key!r} in {sources[source_key]!r} and {component_id!r}"
                )
            sources[source_key] = component_id
            destination_key = (resource.install_root, resource.destination)
            previous_owner = destinations.get(destination_key)
            if previous_owner is not None:
                raise RegistryError(
                    f"duplicate resource destination {resource.install_root}:"
                    f"{resource.destination.as_posix()} in {previous_owner!r} and {component_id!r}"
                )
            destinations[destination_key] = component_id
        components.append(
            InstallComponent(
                id=component_id,
                title=_text(component_data["title"], f"{context}.title"),
                active=_boolean(component_data["active"], f"{context}.active"),
                description=_text(
                    component_data["description"], f"{context}.description"
                ),
                includes=_text(component_data["includes"], f"{context}.includes"),
                depends_on=_string_list(
                    component_data["depends_on"], f"{context}.depends_on"
                ) if "depends_on" in component_data else (),
                resources=resources,
            )
        )
    _validate_dependency_graph(components)
    for component in components:
        if not component.active:
            continue
        for dependency in component.depends_on:
            if not next(item for item in components if item.id == dependency).active:
                raise RegistryError(
                    f"active component {component.id!r} depends on inactive component "
                    f"{dependency!r}"
                )
    return ComponentRegistry(tuple(components), resource_root, repositories)


def resolve_components(
    registry: ComponentRegistry, selected_ids: Iterable[str]
) -> tuple[InstallComponent, ...]:
    """Resolve selected component IDs and their transitive dependencies in registry order."""
    requested = tuple(selected_ids)
    unknown = sorted(set(requested) - set(registry.by_id))
    if unknown:
        raise RegistryError(f"unknown component(s): {', '.join(unknown)}")
    included: set[str] = set()

    def include(component_id: str) -> None:
        if component_id in included:
            return
        for dependency in registry.by_id[component_id].depends_on:
            include(dependency)
        included.add(component_id)

    for component_id in requested:
        include(component_id)
    return tuple(
        component for component in registry.components if component.id in included
    )
