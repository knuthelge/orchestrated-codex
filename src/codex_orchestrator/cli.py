"""Install or remove the Codex Orchestrator skill and custom agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path


RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
MANIFEST_NAME = "codex-orchestrator-install.json"
MANIFEST_VERSION = 2

# Custom agents install under the Codex agents root (CODEX_HOME else ~/.codex).
AGENT_SOURCES = {
    Path("agents/discovery.toml"): RESOURCE_ROOT / "agents/discovery.toml",
    Path("agents/spec-designer.toml"): RESOURCE_ROOT / "agents/spec-designer.toml",
    Path("agents/rubber-duck.toml"): RESOURCE_ROOT / "agents/rubber-duck.toml",
    Path("agents/ui-designer.toml"): RESOURCE_ROOT / "agents/ui-designer.toml",
    Path("agents/tester.toml"): RESOURCE_ROOT / "agents/tester.toml",
    Path("agents/final-reviewer.toml"): RESOURCE_ROOT / "agents/final-reviewer.toml",
}
# The skill installs under a documented Codex skills root anchored to $HOME.
SKILL_SOURCES = {
    Path("orchestrated-delivery"): RESOURCE_ROOT / "skills/orchestrated-delivery",
}


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


def _expand(base: Path, relative: Path, source: Path, files: dict[Path, Path]) -> None:
    destination = base / relative
    if source.is_dir():
        for item in sorted(source.rglob("*")):
            if item.is_file():
                files[destination / item.relative_to(source)] = item
    elif source.is_file():
        files[destination] = source
    else:
        raise FileNotFoundError(f"Missing installation source: {source}")


def source_files(agents_root: Path, skill_root: Path) -> dict[Path, Path]:
    """Map absolute installed destinations to their resource sources across both roots."""
    files: dict[Path, Path] = {}
    for relative, source in AGENT_SOURCES.items():
        _expand(agents_root, relative, source, files)
    for relative, source in SKILL_SOURCES.items():
        _expand(skill_root, relative, source, files)
    return files


def load_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read installation manifest {path}: {error}") from error
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
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def install(agents_root: Path, skill_root: Path) -> int:
    manifest_path = agents_root / MANIFEST_NAME
    previous = load_manifest(manifest_path)
    previous_files = previous.get("files", {}) if previous else {}
    if not isinstance(previous_files, dict):
        raise RuntimeError(f"Invalid files section in {manifest_path}")

    recorded_hashes: dict[Path, str] = {}
    for key, recorded in previous_files.items():
        if not isinstance(key, str) or not isinstance(recorded, str):
            raise RuntimeError(f"Invalid file entry in {manifest_path}")
        recorded_hashes[resolve_recorded_path(key, agents_root, skill_root)] = recorded

    files = source_files(agents_root, skill_root)
    roots = [agents_root, skill_root]

    conflicts: list[Path] = []
    for destination, source in files.items():
        if not destination.exists():
            continue
        recorded = recorded_hashes.get(destination)
        if recorded is None or digest(destination) != recorded:
            if digest(destination) != digest(source):
                conflicts.append(destination)

    if conflicts:
        print("Installation stopped; these files exist and are not unchanged files from this installer:")
        for conflict in conflicts:
            print(f"  {conflict}")
        return 2

    current_paths = set(files)
    obsolete = set(recorded_hashes) - current_paths
    preserved_obsolete: list[Path] = []
    for destination in sorted(obsolete, key=str):
        if not destination.exists():
            continue
        if destination.is_file() and digest(destination) == recorded_hashes[destination]:
            destination.unlink()
            remove_empty_parents(destination.parent, roots)
            print(f"removed obsolete {destination}")
        else:
            preserved_obsolete.append(destination)

    installed: dict[str, str] = {}
    for destination, source in files.items():
        atomic_copy(source, destination)
        installed[destination.as_posix()] = digest(destination)
        print(f"installed {destination}")

    write_manifest(
        manifest_path,
        {"installer": "codex-orchestrator", "version": MANIFEST_VERSION, "files": installed},
    )
    if preserved_obsolete:
        print("Preserved locally modified files that are no longer distributed:")
        for path in preserved_obsolete:
            print(f"  {path}")
    print(f"Installation complete. Restart Codex or start a new conversation.\nManifest: {manifest_path}")
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
    for destination, recorded in sorted(entries, key=lambda item: str(item[0]), reverse=True):
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true", help="Install or update owned files.")
    action.add_argument("--uninstall", action="store_true", help="Remove unchanged installed files.")
    default_root = resolve_agents_root()
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_root,
        help=f"Codex home directory for agents (default: {default_root}).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    agents_root = arguments.codex_home.expanduser().resolve()
    skill_root = resolve_skill_root().expanduser().resolve()
    try:
        if arguments.install:
            return install(agents_root, skill_root)
        return uninstall(agents_root, skill_root)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
