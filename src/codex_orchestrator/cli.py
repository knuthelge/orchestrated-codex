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
INSTALL_SOURCES = {
    Path("skills/orchestrated-delivery"): RESOURCE_ROOT
    / "skills/orchestrated-delivery",
    Path("agents/discovery.toml"): RESOURCE_ROOT / "agents/discovery.toml",
    Path("agents/spec-designer.toml"): RESOURCE_ROOT / "agents/spec-designer.toml",
    Path("agents/ui-designer.toml"): RESOURCE_ROOT / "agents/ui-designer.toml",
    Path("agents/verifier.toml"): RESOURCE_ROOT / "agents/verifier.toml",
    Path("agents/final-reviewer.toml"): RESOURCE_ROOT / "agents/final-reviewer.toml",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_files() -> dict[Path, Path]:
    files: dict[Path, Path] = {}
    for destination, source in INSTALL_SOURCES.items():
        if source.is_dir():
            for item in sorted(source.rglob("*")):
                if item.is_file():
                    files[destination / item.relative_to(source)] = item
        elif source.is_file():
            files[destination] = source
        else:
            raise FileNotFoundError(f"Missing installation source: {source}")
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


def install(codex_root: Path) -> int:
    manifest_path = codex_root / MANIFEST_NAME
    previous = load_manifest(manifest_path)
    previous_files = previous.get("files", {}) if previous else {}
    if not isinstance(previous_files, dict):
        raise RuntimeError(f"Invalid files section in {manifest_path}")

    files = source_files()
    conflicts: list[Path] = []
    for relative, source in files.items():
        destination = codex_root / relative
        if not destination.exists():
            continue
        recorded = previous_files.get(relative.as_posix())
        if not isinstance(recorded, str) or digest(destination) != recorded:
            if digest(destination) != digest(source):
                conflicts.append(destination)

    if conflicts:
        print("Installation stopped; these files exist and are not unchanged files from this installer:")
        for conflict in conflicts:
            print(f"  {conflict}")
        return 2

    current_paths = {relative.as_posix() for relative in files}
    obsolete = set(previous_files) - current_paths
    preserved_obsolete: list[Path] = []
    for relative_text in sorted(obsolete):
        recorded = previous_files[relative_text]
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or not isinstance(recorded, str):
            raise RuntimeError(f"Unsafe or invalid path in {manifest_path}: {relative_text}")
        destination = codex_root / relative
        if not destination.exists():
            continue
        if destination.is_file() and digest(destination) == recorded:
            destination.unlink()
            remove_empty_parents(destination.parent, codex_root)
            print(f"removed obsolete {destination}")
        else:
            preserved_obsolete.append(destination)

    installed: dict[str, str] = {}
    for relative, source in files.items():
        destination = codex_root / relative
        atomic_copy(source, destination)
        installed[relative.as_posix()] = digest(destination)
        print(f"installed {destination}")

    write_manifest(
        manifest_path,
        {"installer": "codex-orchestrator", "version": 1, "files": installed},
    )
    if preserved_obsolete:
        print("Preserved locally modified files that are no longer distributed:")
        for path in preserved_obsolete:
            print(f"  {path}")
    print(f"Installation complete. Restart Codex or start a new conversation.\nManifest: {manifest_path}")
    return 0


def remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def uninstall(codex_root: Path) -> int:
    manifest_path = codex_root / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    if manifest is None:
        print(f"Nothing to uninstall; manifest not found: {manifest_path}")
        return 0
    installed = manifest.get("files", {})
    if not isinstance(installed, dict):
        raise RuntimeError(f"Invalid files section in {manifest_path}")

    preserved: list[Path] = []
    for relative_text, recorded in sorted(installed.items(), reverse=True):
        if not isinstance(relative_text, str) or not isinstance(recorded, str):
            raise RuntimeError(f"Invalid file entry in {manifest_path}")
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe path in {manifest_path}: {relative}")
        destination = codex_root / relative
        if not destination.exists():
            continue
        if not destination.is_file() or digest(destination) != recorded:
            preserved.append(destination)
            continue
        destination.unlink()
        print(f"removed {destination}")
        remove_empty_parents(destination.parent, codex_root)

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
    default_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_root,
        help=f"Codex home directory (default: {default_root}).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    codex_root = arguments.codex_home.expanduser().resolve()
    try:
        return install(codex_root) if arguments.install else uninstall(codex_root)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
