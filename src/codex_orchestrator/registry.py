"""Load and validate the install component registry."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.events import AliasEvent

SCHEMA_VERSION = 1
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


@dataclass(frozen=True)
class InstallResource:
    source: Path
    install_root: str
    destination: Path


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

    @property
    def by_id(self) -> dict[str, InstallComponent]:
        return {component.id: component for component in self.components}


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise RegistryError(f"{context} must be a mapping")
    return value


def _only_keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    unknown = set(value) - expected
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
    ):
        raise RegistryError(f"{context} must be a safe relative path: {text!r}")
    if any(part in ("", ".") for part in candidate.parts):
        raise RegistryError(
            f"{context} must not contain empty or '.' segments: {text!r}"
        )
    return Path(*candidate.parts)


def _string_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RegistryError(f"{context} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{context}[{index}]"))
    if len(result) != len(set(result)):
        raise RegistryError(f"{context} contains duplicates")
    return tuple(result)


def _load_resource(value: object, context: str, resource_root: Path) -> InstallResource:
    data = _mapping(value, context)
    _only_keys(data, {"source", "install_root", "destination"}, context)
    source = _relative_path(data["source"], f"{context}.source")
    destination = _relative_path(data["destination"], f"{context}.destination")
    install_root = _text(data["install_root"], f"{context}.install_root")
    if install_root not in INSTALL_ROOTS:
        raise RegistryError(
            f"{context}.install_root must be one of: {', '.join(sorted(INSTALL_ROOTS))}"
        )
    source_path = resource_root / source
    if not source_path.exists():
        raise RegistryError(f"{context}.source does not exist: {source.as_posix()}")
    if not source_path.resolve().is_relative_to(resource_root.resolve()):
        raise RegistryError(
            f"{context}.source escapes the resource root: {source.as_posix()}"
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
    _only_keys(data, {"schema_version", "components"}, "registry")
    if data["schema_version"] != SCHEMA_VERSION:
        raise RegistryError(
            f"unsupported registry schema version {data['schema_version']!r}; "
            f"expected {SCHEMA_VERSION}"
        )
    raw_components = data["components"]
    if not isinstance(raw_components, list) or not raw_components:
        raise RegistryError("registry.components must be a non-empty list")

    components: list[InstallComponent] = []
    seen_ids: set[str] = set()
    destinations: dict[tuple[str, Path], str] = {}
    component_fields = {
        "id",
        "title",
        "active",
        "description",
        "includes",
        "depends_on",
        "resources",
    }
    for index, raw_component in enumerate(raw_components):
        context = f"registry.components[{index}]"
        component_data = _mapping(raw_component, context)
        _only_keys(component_data, component_fields, context)
        component_id = _text(component_data["id"], f"{context}.id")
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", component_id) is None:
            raise RegistryError(
                f"{context}.id must contain only lowercase letters, digits, and hyphens"
            )
        if component_id in seen_ids:
            raise RegistryError(f"duplicate component id: {component_id}")
        seen_ids.add(component_id)
        raw_resources = component_data["resources"]
        if not isinstance(raw_resources, list) or not raw_resources:
            raise RegistryError(f"{context}.resources must be a non-empty list")
        resources = tuple(
            _load_resource(
                resource, f"{context}.resources[{resource_index}]", resource_root
            )
            for resource_index, resource in enumerate(raw_resources)
        )
        for resource in resources:
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
                ),
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
    return ComponentRegistry(tuple(components), resource_root)


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
