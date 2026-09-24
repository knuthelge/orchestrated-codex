"""Install or remove the Codex Orchestrator skills and custom agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path

import questionary
from prompt_toolkit.layout.controls import FormattedTextControl

from codex_orchestrator.registry import (
    BundledSource,
    ComponentRegistry,
    GitHubSource,
    InstallComponent,
    load_registry,
    resolve_components,
)
from codex_orchestrator.sources import GitHubSourceResolver, SourceError

RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
REGISTRY_PATH = RESOURCE_ROOT / "install-components.yaml"
MANIFEST_NAME = "codex-orchestrator-install.json"
MANIFEST_VERSION = 4
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


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def default_registry() -> ComponentRegistry:
    return load_registry(REGISTRY_PATH, RESOURCE_ROOT)


def _expand(
    base: Path,
    relative: Path,
    source: Path,
    owner: str,
    files: dict[Path, Path],
    owners: dict[Path, set[str]],
    provenance: dict[Path, dict[str, str]] | None = None,
    source_provenance: dict[str, str] | None = None,
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
        if provenance is not None and source_provenance is not None:
            entry = dict(source_provenance)
            if source.is_dir():
                entry["path"] = (Path(entry["path"]) / source_path.relative_to(source)).as_posix()
            provenance[installed_path] = entry

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
    *,
    materialized: dict[str, object] | None = None,
    provenance: dict[Path, dict[str, str]] | None = None,
) -> tuple[dict[Path, Path], dict[Path, set[str]], tuple[InstallComponent, ...]]:
    """Resolve selected components into files, owners, and dependency closure."""
    registry = registry or default_registry()
    selected_ids = component_ids
    if selected_ids is None:
        selected_ids = (
            component.id for component in registry.components
            if component.active and not any(
                isinstance(resource.source, GitHubSource)
                for resource in component.resources
            )
        )
    components = resolve_components(registry, selected_ids)
    files: dict[Path, Path] = {}
    owners: dict[Path, set[str]] = {}
    roots = {"codex_home": agents_root, "skills": skill_root}
    for component in components:
        for resource in component.resources:
            source = resource.source
            if isinstance(source, GitHubSource):
                if materialized is None or source.repository not in materialized:
                    raise RuntimeError(f"Remote source has not been preflighted: {source.repository}")
                repository = (registry.repositories or {})[source.repository]
                result = materialized[source.repository]
                source_path = result.paths[source.path]
                source_info = {
                    "kind": "github", "path": source.path.as_posix(),
                    "owner": repository.owner, "repository": repository.repository,
                    "ref": repository.ref, "sha": result.sha,
                }
            else:
                bundled_path = source.path if isinstance(source, BundledSource) else source
                source_path = registry.resource_root / bundled_path
                source_info = {"kind": "bundled", "path": bundled_path.as_posix()}
            _expand(
                roots[resource.install_root],
                resource.destination,
                source_path,
                component.id,
                files,
                owners,
                provenance,
                source_info,
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
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid installation manifest: {path}")
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
        destination = resolve_recorded_path(key, agents_root, skill_root)
        if destination in recorded_hashes:
            raise RuntimeError(f"Duplicate file path in {manifest_path}: {key}")
        recorded_hashes[destination] = recorded
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


def _recorded_provenance(
    manifest: dict[str, object] | None,
    manifest_path: Path,
    agents_root: Path,
    skill_root: Path,
    recorded_hashes: dict[Path, str],
) -> dict[Path, dict[str, str]]:
    if not manifest:
        return {}
    version = manifest.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version not in (1, 2, 3, 4):
        raise RuntimeError(f"Unsupported manifest version in {manifest_path}")
    if version < 4:
        return {}
    raw = manifest.get("file_provenance")
    if not isinstance(raw, dict):
        raise RuntimeError(f"Invalid file_provenance section in {manifest_path}")
    result: dict[Path, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise RuntimeError(f"Invalid file provenance entry in {manifest_path}")
        kind = value.get("kind")
        keys = set(value)
        if kind == "legacy":
            valid = keys == {"kind"}
        elif kind == "bundled":
            valid = keys == {"kind", "path"} and _provenance_path(value.get("path"))
        elif kind == "github":
            valid = (keys == {"kind", "path", "owner", "repository", "ref", "sha"}
                     and _provenance_path(value.get("path"))
                     and _github_owner(value.get("owner"))
                     and _github_repository(value.get("repository"))
                     and _github_ref(value.get("ref"))
                     and isinstance(value["sha"], str)
                     and re.fullmatch(r"[0-9a-fA-F]{40}", value["sha"]) is not None)
        else:
            valid = False
        if not valid:
            raise RuntimeError(f"Invalid file provenance entry in {manifest_path}: {key}")
        destination = resolve_recorded_path(key, agents_root, skill_root)
        if destination in result:
            raise RuntimeError(f"Duplicate file provenance path in {manifest_path}: {key}")
        result[destination] = value.copy()
    if set(result) != set(recorded_hashes):
        raise RuntimeError(f"file_provenance must match files exactly in {manifest_path}")
    return result


def _provenance_path(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and not Path(value).is_absolute()
            and all(part not in ("", ".", "..") for part in value.split("/"))
            and not any(char in value for char in ("\\", ":"))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _github_owner(value: object) -> bool:
    return (isinstance(value, str) and
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", value) is not None)


def _github_repository(value: object) -> bool:
    return (isinstance(value, str) and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None
            and not value.endswith(".git"))


def _github_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and ".." not in value
        and all(
            re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", segment) is not None
            and not segment.endswith((".", ".lock"))
            for segment in value.split("/")
        )
    )


def _recorded_ancestor_conflicts(paths: Iterable[Path], roots: Sequence[Path]) -> list[Path]:
    """Reject tracked paths reached through a non-directory ancestor."""
    conflicts: set[Path] = set()
    for path in paths:
        lexical_root = next(
            (candidate for candidate in reversed(path.parents)
             if any(candidate.resolve() == root.resolve() for root in roots)),
            None,
        )
        if lexical_root is None:
            conflicts.add(path)
            continue
        for ancestor in (lexical_root, *(
            lexical_root / part for part in reversed(path.relative_to(lexical_root).parents)
        )):
            try:
                mode = ancestor.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(mode) and not (
                ancestor == lexical_root and stat.S_ISLNK(mode) and ancestor.is_dir()
            ):
                conflicts.add(ancestor)
    return sorted(conflicts, key=str)


def _path_conflicts(planned: set[Path], recorded: set[Path], roots: Sequence[Path]) -> list[Path]:
    """Find shape and prefix conflicts without modifying installed content."""
    conflicts: set[Path] = set()
    all_paths = planned | recorded
    for path in planned:
        if any(path != other and (path in other.parents or other in path.parents)
               for other in all_paths):
            conflicts.add(path)
        root = next((root for root in roots if path.is_relative_to(root)), None)
        if root is None:
            conflicts.add(path)
            continue
        for ancestor in (root, *reversed(path.relative_to(root).parents)):
            candidate = root / ancestor if not ancestor.is_absolute() else ancestor
            if candidate == path or not candidate.exists() and not candidate.is_symlink():
                continue
            mode = candidate.lstat().st_mode
            if not stat.S_ISDIR(mode) and not (
                candidate == root and stat.S_ISLNK(mode) and candidate.is_dir()
            ):
                conflicts.add(candidate)
        if path.exists() or path.is_symlink():
            if not stat.S_ISREG(path.lstat().st_mode):
                conflicts.add(path)
    return sorted(conflicts, key=str)


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
        if any(isinstance(resource.source, GitHubSource) for resource in component.resources):
            continue
        component_paths = set(
            source_files(agents_root, skill_root, (component.id,), registry)
        )
        if recorded_paths & component_paths:
            inferred.add(component.id)
    return inferred


@contextmanager
def _remote_source_status(enabled: bool):
    """Show a temporary TTY status while interactive remote sources are fetched."""
    stream = sys.stdout
    if not enabled or not stream.isatty():
        yield
        return

    frames = ("|", "/", "-", "\\")
    message = "Fetching selected third-party sources"
    stopped = threading.Event()

    def animate() -> None:
        frame = 1
        while not stopped.wait(0.12):
            stream.write(f"\r{frames[frame % len(frames)]} {message}")
            stream.flush()
            frame += 1

    stream.write(f"\r{frames[0]} {message}")
    stream.flush()
    worker = threading.Thread(target=animate, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        stream.write("\r\033[2K")
        stream.flush()


def install(
    agents_root: Path,
    skill_root: Path,
    component_ids: Iterable[str] | None = None,
    registry: ComponentRegistry | None = None,
    *,
    preserve_existing_remote: bool = False,
    show_remote_status: bool = False,
) -> int:
    registry = registry or default_registry()
    selected_ids = tuple(component_ids) if component_ids is not None else tuple(
        component.id for component in registry.components
        if component.active and not any(
            isinstance(resource.source, GitHubSource)
            for resource in component.resources
        )
    )
    manifest_path = agents_root / MANIFEST_NAME
    previous = load_manifest(manifest_path)
    recorded = _recorded_hashes(previous, manifest_path, agents_root, skill_root)
    _recorded_provenance(previous, manifest_path, agents_root, skill_root, recorded)
    ancestor_conflicts = _recorded_ancestor_conflicts(recorded, (agents_root, skill_root))
    if ancestor_conflicts:
        raise RuntimeError(
            "Tracked installation paths have incompatible ancestors: "
            + ", ".join(str(path) for path in ancestor_conflicts)
        )
    selected = resolve_components(registry, selected_ids)
    if not selected:
        raise RuntimeError("At least one component must be selected")
    selected_sources: dict[str, set[Path]] = {}
    for component in selected:
        for resource in component.resources:
            if isinstance(resource.source, GitHubSource):
                selected_sources.setdefault(resource.source.repository, set()).add(resource.source.path)
    with tempfile.TemporaryDirectory(prefix="codex-orchestrator-sources-") as temporary:
        materialized: dict[str, object] = {}
        if selected_sources:
            with _remote_source_status(show_remote_status):
                resolver = GitHubSourceResolver()
                for repository_id, paths in selected_sources.items():
                    repository = (registry.repositories or {})[repository_id]
                    try:
                        materialized[repository_id] = resolver.materialize(
                            repository, sorted(paths), Path(temporary) / repository_id
                        )
                    except SourceError as error:
                        raise SourceError(
                            f"{repository.owner}/{repository.repository}@{repository.ref}: {error}"
                        ) from error
        return _install_resolved(
            agents_root, skill_root, selected_ids, registry, materialized,
            preserve_existing_remote,
        )


def _install_resolved(
    agents_root: Path,
    skill_root: Path,
    component_ids: tuple[str, ...],
    registry: ComponentRegistry,
    materialized: dict[str, object],
    preserve_existing_remote: bool,
) -> int:
    manifest_path = agents_root / MANIFEST_NAME
    previous = load_manifest(manifest_path)
    recorded_hashes = _recorded_hashes(previous, manifest_path, agents_root, skill_root)
    previous_owners = _recorded_owners(previous, manifest_path, agents_root, skill_root)
    previous_provenance = _recorded_provenance(
        previous, manifest_path, agents_root, skill_root, recorded_hashes
    )
    ancestor_conflicts = _recorded_ancestor_conflicts(
        recorded_hashes, (agents_root, skill_root)
    )
    if ancestor_conflicts:
        raise RuntimeError(
            "Tracked installation paths have incompatible ancestors: "
            + ", ".join(str(path) for path in ancestor_conflicts)
        )
    retained_remote_ids: set[str] = set()
    retained_remote_paths: set[Path] = set()
    if preserve_existing_remote and previous:
        remote_ids = {
            component.id for component in registry.components
            if any(isinstance(resource.source, GitHubSource)
                   for resource in component.resources)
        }
        retained_remote_ids = installed_component_ids(
            previous, agents_root, skill_root, registry
        ) & remote_ids
        retained_remote_paths = {
            destination for destination in recorded_hashes
            if set(previous_owners.get(destination, ())) & retained_remote_ids
            or previous_provenance.get(destination, {}).get("kind") == "github"
        }
    if not set(previous_owners).issubset(recorded_hashes):
        raise RuntimeError(f"File owners reference an unknown file in {manifest_path}")

    provenance: dict[Path, dict[str, str]] = {}
    if materialized:
        files, owners, components = source_plan(
            agents_root, skill_root, component_ids, registry,
            materialized=materialized, provenance=provenance,
        )
    else:
        files, owners, components = source_plan(
            agents_root, skill_root, component_ids, registry
        )
        for destination, source in files.items():
            try:
                relative = source.relative_to(registry.resource_root)
            except ValueError:
                provenance[destination] = {"kind": "legacy"}
            else:
                provenance[destination] = {"kind": "bundled", "path": relative.as_posix()}
    if not components:
        raise RuntimeError("At least one component must be selected")
    roots = [agents_root, skill_root]
    shape_conflicts = _path_conflicts(set(files), set(recorded_hashes), roots)
    if shape_conflicts:
        print("Installation stopped; destination shape conflicts:")
        for conflict in shape_conflicts:
            print(f"  {conflict}")
        return 2
    source_hashes = {
        destination: digest(source) for destination, source in files.items()
    }
    destination_hashes = {
        destination: digest(destination)
        for destination in files
        if _regular_file(destination)
    }

    conflicts: list[Path] = []
    for destination in files:
        if not destination.exists() and not destination.is_symlink():
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
    obsolete = set(recorded_hashes) - current_paths - retained_remote_paths
    removed: list[Path] = []
    preserved_obsolete: list[Path] = []
    for destination in sorted(obsolete, key=str):
        if not destination.exists() and not destination.is_symlink():
            continue
        if (
            _regular_file(destination)
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
        for destination in preserved_obsolete + sorted(retained_remote_paths, key=str)
    }
    installed_owners: dict[str, list[str]] = {
        destination.as_posix(): previous_owners.get(destination, [])
        for destination in preserved_obsolete + sorted(retained_remote_paths, key=str)
    }
    installed_provenance: dict[str, dict[str, str]] = {
        destination.as_posix(): previous_provenance.get(destination, {"kind": "legacy"})
        for destination in preserved_obsolete + sorted(retained_remote_paths, key=str)
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
        installed_provenance[destination.as_posix()] = provenance.get(destination, {"kind": "legacy"})

    write_manifest(
        manifest_path,
        {
            "installer": "codex-orchestrator",
            "version": MANIFEST_VERSION,
            "components": [component.id for component in components] + sorted(retained_remote_ids - {component.id for component in components}),
            "files": installed,
            "file_owners": installed_owners,
            "file_provenance": installed_provenance,
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
    installed = _recorded_hashes(manifest, manifest_path, agents_root, skill_root)
    _recorded_provenance(manifest, manifest_path, agents_root, skill_root, installed)
    ancestor_conflicts = _recorded_ancestor_conflicts(
        installed, (agents_root, skill_root)
    )
    if ancestor_conflicts:
        raise RuntimeError(
            "Tracked installation paths have incompatible ancestors: "
            + ", ".join(str(path) for path in ancestor_conflicts)
        )

    roots = [agents_root, skill_root]
    entries = list(installed.items())

    preserved: list[Path] = []
    for destination, recorded in sorted(
        entries, key=lambda item: str(item[0]), reverse=True
    ):
        if not destination.exists() and not destination.is_symlink():
            continue
        if not _regular_file(destination) or digest(destination) != recorded:
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
    first_party = tuple(
        component for component in visible_components
        if not any(isinstance(resource.source, GitHubSource) for resource in component.resources)
    )
    third_party = tuple(
        component for component in visible_components
        if any(isinstance(resource.source, GitHubSource) for resource in component.resources)
    )
    print()
    print(f"{BOLD}Choose the Codex components to install, update, or remove.{RESET}")
    print()
    print(f"{BOLD}Available components:{RESET}")
    for heading, components in (("First-party", first_party), ("Third-party", third_party)):
        if not components:
            continue
        if heading == "Third-party" and first_party:
            print()
        print(f"{BOLD}{heading}:{RESET}")
        for component in components:
            retired_note = " [retired]" if not component.active else ""
            print(
                f"  {BOLD}{component.title}{RESET} - {component.description}{retired_note}"
            )
            remote = [resource.source for resource in component.resources
                      if isinstance(resource.source, GitHubSource)]
            for repository_id in dict.fromkeys(source.repository for source in remote):
                repository = (registry.repositories or {})[repository_id]
                print(f"    Third-party: {repository.display_name} ({repository.owner}/{repository.repository})")
                print(f"    {repository.homepage} | Licence: {repository.licence}")
                if component.id == "grill-me" and any(
                    source.path.parent.name == "grilling" and source.path.name == "SKILL.md"
                    for source in remote
                ):
                    print("    Selecting Grill me also installs the upstream grilling companion directory.")
    print()
    choices: list[questionary.Choice] = []
    for heading, components in (("First-party", first_party), ("Third-party", third_party)):
        if not components:
            continue
        if heading == "Third-party" and first_party:
            choices.append(questionary.Separator("── Third-party ──"))
        for component in components:
            statuses: list[str] = []
            if component.depends_on:
                dependency_titles = [
                    registry.by_id[dependency].title for dependency in component.depends_on
                ]
                statuses.append(f"requires {', '.join(dependency_titles)}")
            if not component.active:
                statuses.append("retired; deselect to remove")
            includes = f" - {component.includes.rstrip('.')}."
            if heading == "Third-party":
                includes += " (3rd party)"
            title: list[tuple[str, str]] = [
                ("class:component-title", component.title),
                ("class:component-detail", includes),
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
            selected_ids = set(selected)
            return tuple(
                component.id for component in visible_components
                if component.id in selected_ids
            )
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
        help="Install all bundled components without an interactive prompt.",
    )
    parser.add_argument(
        "--include-third-party",
        action="store_true",
        help="With --install --all, also install all third-party components.",
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
    if arguments.include_third_party and not (arguments.install and arguments.all):
        parser.error("--include-third-party requires --install --all")
    return arguments


def argument_error(message: str) -> int:
    """Print an argparse-style command input error and return its exit status."""
    parser = build_parser()
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: error: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    agents_root = arguments.codex_home.expanduser().absolute()
    skill_root = resolve_skill_root().expanduser().absolute()
    try:
        if arguments.install:
            registry = default_registry()
            if arguments.all:
                selected_ids = tuple(
                    component.id
                    for component in registry.components
                    if component.active and (
                        arguments.include_third_party or not any(
                            isinstance(resource.source, GitHubSource)
                            for resource in component.resources
                        )
                    )
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
            return install(
                agents_root, skill_root, selected_ids, registry,
                preserve_existing_remote=arguments.all and not arguments.include_third_party,
                show_remote_status=not arguments.all and not arguments.components,
            )
        return uninstall(agents_root, skill_root)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
