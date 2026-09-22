"""Install or remove the Codex Orchestrator skills and custom agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import questionary
from prompt_toolkit.layout.controls import FormattedTextControl

from codex_orchestrator.registry import (
    ComponentRegistry,
    InstallComponent,
    load_registry,
    resolve_components,
)

RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
REGISTRY_PATH = RESOURCE_ROOT / "install-components.yaml"
MANIFEST_NAME = "codex-orchestrator-install.json"
MANIFEST_VERSION = 3
INSTALLER_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("pointer", "fg:ansicyan bold"),
        ("selected", "noreverse fg:ansigreen"),
        ("text", "noreverse fg:ansiwhite"),
        ("instruction", "fg:ansibrightblack"),
        ("component-title", "bold"),
        ("component-detail", "fg:ansibrightblack"),
        ("component-status", "fg:ansiyellow"),
        ("validation-toolbar", "fg:ansired bold"),
    ]
)
BOLD = "\033[1m"
RESET = "\033[0m"


def _hide_prompt_cursor(prompt: questionary.Question) -> None:
    """Keep the terminal cursor from covering the focused choice marker."""
    for control in prompt.application.layout.find_all_controls():
        if isinstance(control, FormattedTextControl):
            control.show_cursor = False


def resolve_agents_root() -> Path:
    """Codex home hosting agents/*.toml: CODEX_HOME if set, else ~/.codex."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def resolve_skill_root() -> Path:
    """Documented Codex skills root, anchored to $HOME (not CODEX_HOME)."""
    return Path.home() / ".agents" / "skills"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def default_registry() -> ComponentRegistry:
    return load_registry(REGISTRY_PATH, RESOURCE_ROOT)


def _expand(
    base: Path,
    relative: Path,
    source: Path,
    owner: str,
    files: dict[Path, Path],
    owners: dict[Path, set[str]],
) -> None:
    destination = base / relative
    source_boundary = source.resolve() if source.is_dir() else source.parent.resolve()

    def add(installed_path: Path, source_path: Path) -> None:
        if not installed_path.resolve().is_relative_to(base.resolve()):
            raise RuntimeError(
                f"Installation destination escapes its root: {installed_path}"
            )
        if not source_path.resolve().is_relative_to(source_boundary):
            raise RuntimeError(
                f"Installation source escapes its resource: {source_path}"
            )
        if installed_path in files:
            raise RuntimeError(
                f"Duplicate expanded resource destination: {installed_path}"
            )
        files[installed_path] = source_path
        owners.setdefault(installed_path, set()).add(owner)

    if source.is_dir():
        for item in sorted(source.rglob("*")):
            if item.is_file():
                installed_path = destination / item.relative_to(source)
                add(installed_path, item)
    elif source.is_file():
        add(destination, source)
    else:
        raise FileNotFoundError(f"Missing installation source: {source}")


def source_plan(
    agents_root: Path,
    skill_root: Path,
    component_ids: Iterable[str] | None = None,
    registry: ComponentRegistry | None = None,
) -> tuple[dict[Path, Path], dict[Path, set[str]], tuple[InstallComponent, ...]]:
    """Resolve selected components into files, owners, and dependency closure."""
    registry = registry or default_registry()
    selected_ids = component_ids
    if selected_ids is None:
        selected_ids = (
            component.id for component in registry.components if component.active
        )
    components = resolve_components(registry, selected_ids)
    files: dict[Path, Path] = {}
    owners: dict[Path, set[str]] = {}
    roots = {"codex_home": agents_root, "skills": skill_root}
    for component in components:
        for resource in component.resources:
            _expand(
                roots[resource.install_root],
                resource.destination,
                registry.resource_root / resource.source,
                component.id,
                files,
                owners,
            )
    return files, owners, components


def source_files(
    agents_root: Path,
    skill_root: Path,
    component_ids: Iterable[str] | None = None,
    registry: ComponentRegistry | None = None,
) -> dict[Path, Path]:
    """Map selected components' absolute destinations to packaged sources."""
    files, _, _ = source_plan(agents_root, skill_root, component_ids, registry)
    return files


def load_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Cannot read installation manifest {path}: {error}"
        ) from error
    if data.get("installer") != "codex-orchestrator":
        raise RuntimeError(f"Refusing to use an unrecognized manifest: {path}")
    return data


def resolve_recorded_path(key: str, agents_root: Path, skill_root: Path) -> Path:
    """Resolve a manifest key to an absolute path.

    Version 2 manifests record absolute paths. Legacy (version 1) manifests recorded
    paths relative to the Codex home, so those resolve against the agents root. A
    manifest path is usable only when its canonical location remains below one of
    the two installation roots.
    """
    recorded = Path(key)
    if recorded.is_absolute():
        destination = recorded
    else:
        if ".." in recorded.parts:
            raise RuntimeError(f"Unsafe path in manifest: {key}")
        destination = agents_root / recorded

    resolved_destination = destination.resolve()
    resolved_roots = (agents_root.resolve(), skill_root.resolve())
    if not any(resolved_destination.is_relative_to(root) for root in resolved_roots):
        raise RuntimeError(f"Path outside installation roots in manifest: {key}")
    return destination


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_empty_parents(path: Path, roots: Sequence[Path]) -> None:
    current = path
    while True:
        if current in roots:
            return
        if not any(root in current.parents for root in roots):
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def print_install_report(
    *,
    agents_root: Path,
    skill_root: Path,
    added: Sequence[Path],
    updated: Sequence[Path],
    removed: Sequence[Path],
    unchanged: Sequence[Path],
    preserved: Sequence[Path],
) -> None:
    def display_path(path: Path) -> str:
        if path.is_relative_to(agents_root):
            return path.relative_to(agents_root).as_posix()
        if path.is_relative_to(skill_root):
            relative = path.relative_to(skill_root).as_posix()
            return f".agents/skills/{relative}"
        return str(path)

    print("Changes:")
    categories = (
        ("Added", "+", added),
        ("Updated", "~", updated),
        ("Removed", "-", removed),
        ("Unchanged", "", unchanged),
        ("Preserved", "!", preserved),
    )
    for label, marker, paths in categories:
        print(f"  {label:<9} {len(paths)}")
        if marker:
            for path in sorted(paths, key=str):
                print(f"    {marker} {display_path(path)}")
    print()


def _recorded_hashes(
    manifest: dict[str, object] | None,
    manifest_path: Path,
    agents_root: Path,
    skill_root: Path,
) -> dict[Path, str]:
    previous_files = manifest.get("files", {}) if manifest else {}
    if not isinstance(previous_files, dict):
        raise RuntimeError(f"Invalid files section in {manifest_path}")
    recorded_hashes: dict[Path, str] = {}
    for key, recorded in previous_files.items():
        if not isinstance(key, str) or not isinstance(recorded, str):
            raise RuntimeError(f"Invalid file entry in {manifest_path}")
        recorded_hashes[resolve_recorded_path(key, agents_root, skill_root)] = recorded
    return recorded_hashes


def _recorded_owners(
    manifest: dict[str, object] | None,
    manifest_path: Path,
    agents_root: Path,
    skill_root: Path,
) -> dict[Path, list[str]]:
    if not manifest or "file_owners" not in manifest:
        return {}
    raw_owners = manifest["file_owners"]
    if not isinstance(raw_owners, dict):
        raise RuntimeError(f"Invalid file_owners section in {manifest_path}")
    owners: dict[Path, list[str]] = {}
    for key, component_ids in raw_owners.items():
        if (
            not isinstance(key, str)
            or not isinstance(component_ids, list)
            or not all(isinstance(component_id, str) for component_id in component_ids)
            or len(component_ids) != len(set(component_ids))
        ):
            raise RuntimeError(f"Invalid file owner entry in {manifest_path}")
        owners[resolve_recorded_path(key, agents_root, skill_root)] = component_ids
    return owners


def installed_component_ids(
    manifest: dict[str, object] | None,
    agents_root: Path,
    skill_root: Path,
    registry: ComponentRegistry,
) -> set[str]:
    """Get installed components, inferring them for legacy flat manifests."""
    if manifest is None:
        return set()
    if "components" in manifest:
        components = manifest["components"]
        if not isinstance(components, list) or not all(
            isinstance(component_id, str) for component_id in components
        ):
            raise RuntimeError("Invalid components section in installation manifest")
        return set(components) & set(registry.by_id)

    manifest_path = agents_root / MANIFEST_NAME
    recorded_paths = set(
        _recorded_hashes(manifest, manifest_path, agents_root, skill_root)
    )
    inferred: set[str] = set()
    for component in registry.components:
        component_paths = set(
            source_files(agents_root, skill_root, (component.id,), registry)
        )
        if recorded_paths & component_paths:
            inferred.add(component.id)
    return inferred


def install(
    agents_root: Path,
    skill_root: Path,
    component_ids: Iterable[str] | None = None,
    registry: ComponentRegistry | None = None,
) -> int:
    registry = registry or default_registry()
    manifest_path = agents_root / MANIFEST_NAME
    previous = load_manifest(manifest_path)
    recorded_hashes = _recorded_hashes(previous, manifest_path, agents_root, skill_root)
    previous_owners = _recorded_owners(previous, manifest_path, agents_root, skill_root)
    if not set(previous_owners).issubset(recorded_hashes):
        raise RuntimeError(f"File owners reference an unknown file in {manifest_path}")

    files, owners, components = source_plan(
        agents_root, skill_root, component_ids, registry
    )
    if not components:
        raise RuntimeError("At least one component must be selected")
    roots = [agents_root, skill_root]
    source_hashes = {
        destination: digest(source) for destination, source in files.items()
    }
    destination_hashes = {
        destination: digest(destination)
        for destination in files
        if destination.is_file()
    }

    conflicts: list[Path] = []
    for destination in files:
        if not destination.exists():
            continue
        recorded = recorded_hashes.get(destination)
        current = destination_hashes.get(destination)
        if (recorded is None or current != recorded) and current != source_hashes[
            destination
        ]:
            conflicts.append(destination)

    if conflicts:
        print(
            "Installation stopped; these files exist and are not unchanged files from this installer:"
        )
        for conflict in conflicts:
            print(f"  {conflict}")
        return 2

    current_paths = set(files)
    obsolete = set(recorded_hashes) - current_paths
    removed: list[Path] = []
    preserved_obsolete: list[Path] = []
    for destination in sorted(obsolete, key=str):
        if not destination.exists():
            continue
        if (
            destination.is_file()
            and digest(destination) == recorded_hashes[destination]
        ):
            destination.unlink()
            remove_empty_parents(destination.parent, roots)
            removed.append(destination)
        else:
            preserved_obsolete.append(destination)

    added: list[Path] = []
    updated: list[Path] = []
    unchanged: list[Path] = []
    installed: dict[str, str] = {
        destination.as_posix(): recorded_hashes[destination]
        for destination in preserved_obsolete
    }
    installed_owners: dict[str, list[str]] = {
        destination.as_posix(): previous_owners.get(destination, [])
        for destination in preserved_obsolete
    }
    for destination, source in files.items():
        source_hash = source_hashes[destination]
        current_hash = destination_hashes.get(destination)
        if current_hash == source_hash:
            unchanged.append(destination)
        else:
            atomic_copy(source, destination)
            if current_hash is None:
                added.append(destination)
            else:
                updated.append(destination)
        installed[destination.as_posix()] = source_hash
        installed_owners[destination.as_posix()] = sorted(owners[destination])

    write_manifest(
        manifest_path,
        {
            "installer": "codex-orchestrator",
            "version": MANIFEST_VERSION,
            "components": [component.id for component in components],
            "files": installed,
            "file_owners": installed_owners,
        },
    )
    print_install_report(
        agents_root=agents_root,
        skill_root=skill_root,
        added=added,
        updated=updated,
        removed=removed,
        unchanged=unchanged,
        preserved=preserved_obsolete,
    )
    component_names = ", ".join(component.title for component in components)
    print(
        f"Installation complete: {component_names}.\n"
        f"Restart Codex or start a new conversation.\nManifest: {manifest_path}"
    )
    return 0


def uninstall(agents_root: Path, skill_root: Path) -> int:
    manifest_path = agents_root / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    if manifest is None:
        print(f"Nothing to uninstall; manifest not found: {manifest_path}")
        return 0
    installed = manifest.get("files", {})
    if not isinstance(installed, dict):
        raise RuntimeError(f"Invalid files section in {manifest_path}")

    roots = [agents_root, skill_root]
    entries: list[tuple[Path, str]] = []
    for key, recorded in installed.items():
        if not isinstance(key, str) or not isinstance(recorded, str):
            raise RuntimeError(f"Invalid file entry in {manifest_path}")
        entries.append((resolve_recorded_path(key, agents_root, skill_root), recorded))

    preserved: list[Path] = []
    for destination, recorded in sorted(
        entries, key=lambda item: str(item[0]), reverse=True
    ):
        if not destination.exists():
            continue
        if not destination.is_file() or digest(destination) != recorded:
            preserved.append(destination)
            continue
        destination.unlink()
        print(f"removed {destination}")
        remove_empty_parents(destination.parent, roots)

    if preserved:
        print("Preserved locally modified installed files:")
        for path in preserved:
            print(f"  {path}")
        print(f"Manifest retained: {manifest_path}")
        return 2

    manifest_path.unlink(missing_ok=True)
    print("Uninstall complete.")
    return 0


def select_components(
    registry: ComponentRegistry, preselected: set[str]
) -> tuple[str, ...] | None:
    """Show the guided component chooser, or return None when cancelled."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError(
            "--install requires an interactive terminal; use --all or --components for "
            "an unattended install"
        )
    visible_ids = {
        component.id for component in resolve_components(registry, preselected)
    }
    visible_components = tuple(
        component
        for component in registry.components
        if component.active or component.id in visible_ids
    )
    print()
    print(f"{BOLD}Choose the Codex components to install, update, or remove.{RESET}")
    print()
    print(f"{BOLD}Available components:{RESET}")
    for component in visible_components:
        retired_note = " [retired]" if not component.active else ""
        print(
            f"  {BOLD}{component.title}{RESET} - {component.description}{retired_note}"
        )
    print()
    choices: list[questionary.Choice] = []
    for component in visible_components:
        statuses: list[str] = []
        if component.depends_on:
            dependency_titles = [
                registry.by_id[dependency].title for dependency in component.depends_on
            ]
            statuses.append(f"requires {', '.join(dependency_titles)}")
        if not component.active:
            statuses.append("retired; deselect to remove")
        title: list[tuple[str, str]] = [
            ("class:component-title", component.title),
            ("class:component-detail", f" - {component.includes.rstrip('.')}."),
        ]
        if statuses:
            title.append(("class:component-status", f"  [{'; '.join(statuses)}]"))
        choices.append(
            questionary.Choice(
                title=title,
                value=component.id,
                checked=component.id in preselected,
            )
        )
    while True:
        try:
            prompt = questionary.checkbox(
                "Select components to install",
                choices=choices,
                instruction="(↑/↓ move, Space toggle, Enter install)",
                qmark="◆",
                pointer="›",
                style=INSTALLER_STYLE,
                validate=lambda selected_ids: validate_component_selection(
                    registry, selected_ids
                ),
            )
            _hide_prompt_cursor(prompt)
            selected = prompt.ask()
        except KeyboardInterrupt:
            return None
        if selected is None:
            return None
        if selected:
            return tuple(selected)
        print("Select at least one component, or press Ctrl-C to cancel.")


def validate_component_selection(
    registry: ComponentRegistry, selected_ids: Sequence[str]
) -> bool | str:
    """Validate an interactive selection without silently adding dependencies."""
    if not selected_ids:
        return "Select at least one component, or press Ctrl-C to cancel."
    selected = set(selected_ids)
    missing = [
        component.title
        for component in resolve_components(registry, selected_ids)
        if component.id not in selected
    ]
    if missing:
        return "Select required dependencies: " + ", ".join(missing)
    return True


def parse_component_ids(value: str) -> tuple[str, ...]:
    component_ids = tuple(part.strip() for part in value.split(",") if part.strip())
    if not component_ids:
        raise argparse.ArgumentTypeError(
            "expected one or more comma-separated component IDs"
        )
    if len(component_ids) != len(set(component_ids)):
        raise argparse.ArgumentTypeError("component IDs must not be repeated")
    return component_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--install", action="store_true", help="Install or update owned files."
    )
    action.add_argument(
        "--uninstall", action="store_true", help="Remove unchanged installed files."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--all",
        action="store_true",
        help="Install all registered components without an interactive prompt.",
    )
    selection.add_argument(
        "--components",
        type=parse_component_ids,
        metavar="ID[,ID...]",
        help="Install named components and their dependencies without an interactive prompt.",
    )
    default_root = resolve_agents_root()
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_root,
        help=f"Codex home directory for agents (default: {default_root}).",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if (arguments.all or arguments.components) and not arguments.install:
        parser.error("--all and --components can only be used with --install")
    return arguments


def argument_error(message: str) -> int:
    """Print an argparse-style command input error and return its exit status."""
    parser = build_parser()
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: error: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    agents_root = arguments.codex_home.expanduser().resolve()
    skill_root = resolve_skill_root().expanduser().resolve()
    try:
        if arguments.install:
            registry = default_registry()
            if arguments.all:
                selected_ids = tuple(
                    component.id
                    for component in registry.components
                    if component.active
                )
            elif arguments.components:
                selected_ids = arguments.components
                unknown = [
                    component_id
                    for component_id in selected_ids
                    if component_id not in registry.by_id
                ]
                if unknown:
                    return argument_error(
                        "argument --components: unknown component ID(s): "
                        + ", ".join(unknown)
                    )
                inactive = [
                    component_id
                    for component_id in selected_ids
                    if component_id in registry.by_id
                    and not registry.by_id[component_id].active
                ]
                if inactive:
                    return argument_error(
                        "argument --components: inactive component ID(s): "
                        + ", ".join(inactive)
                    )
            else:
                manifest = load_manifest(agents_root / MANIFEST_NAME)
                preselected = installed_component_ids(
                    manifest, agents_root, skill_root, registry
                )
                selected_ids = select_components(registry, preselected)
                if selected_ids is None:
                    print("Installation cancelled.")
                    return 0
            resolved = resolve_components(registry, selected_ids)
            automatically_included = [
                component.title
                for component in resolved
                if component.id not in selected_ids
            ]
            if automatically_included:
                print(
                    "Also including required dependencies: "
                    + ", ".join(automatically_included)
                )
            return install(agents_root, skill_root, selected_ids, registry)
        return uninstall(agents_root, skill_root)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
