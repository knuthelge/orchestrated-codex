"""Fetch explicitly listed GitHub skill files at one resolved commit."""

from __future__ import annotations

import http.client
import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, urlsplit

from .registry import GitHubRepository


class SourceError(RuntimeError):
    """A selected GitHub source failed validation or retrieval."""


@dataclass(frozen=True)
class SourceLimits:
    connect_seconds: float = 5
    read_seconds: float = 15
    request_seconds: float = 60
    ref_bytes: int = 1024 * 1024
    file_bytes: int = 32 * 1024 * 1024
    expanded_bytes: int = 128 * 1024 * 1024
    files: int = 10_000
    path_components: int = 32
    path_bytes: int = 1024


@dataclass(frozen=True)
class MaterializedRepository:
    sha: str
    paths: Mapping[Path, Path]


# URL, byte limit, expected host, monotonic deadline, Accept media type.
Fetch = Callable[[str, int, str, float, str], bytes]
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_JSON_ACCEPT = "application/vnd.github+json"
_RAW_ACCEPT = "application/octet-stream"
_API_VERSION = "2022-11-28"
_ALLOWED_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com"})


def _http_status_error(response: http.client.HTTPResponse) -> SourceError:
    status = response.status
    if status not in (403, 429):
        return SourceError(f"GitHub returned HTTP {status}")

    remaining = response.getheader("X-RateLimit-Remaining")
    reset = response.getheader("X-RateLimit-Reset")
    retry_after = response.getheader("Retry-After")
    if remaining == "0":
        detail = "GitHub API rate limit reached"
        if reset and reset.isdecimal():
            try:
                when = datetime.fromtimestamp(int(reset), timezone.utc).astimezone()
                detail += f"; retry after {when:%Y-%m-%d %H:%M %Z} (local time)"
            except (OverflowError, OSError, ValueError):
                pass
    elif retry_after and retry_after.isdecimal():
        detail = f"GitHub rate limited this request; retry after {retry_after} seconds"
    else:
        detail = "GitHub denied this request; it may be rate limited"
    return SourceError(f"{detail} (HTTP {status})")


class HTTPSFetcher:
    """Bounded HTTPS GET that never follows redirects."""

    def __init__(
        self,
        limits: SourceLimits = SourceLimits(),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = limits
        self.clock = clock

    def __call__(
        self,
        url: str,
        max_bytes: int,
        expected_host: str,
        deadline: float,
        accept: str = _JSON_ACCEPT,
    ) -> bytes:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or expected_host not in _ALLOWED_HOSTS
            or parts.hostname != expected_host
            or parts.port not in (None, 443)
            or parts.username
            or parts.password
            or not parts.path.startswith("/")
            or parts.query
            or parts.fragment
        ):
            raise SourceError("unexpected GitHub request URL")
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise SourceError("GitHub request deadline exceeded")
        connection = http.client.HTTPSConnection(
            expected_host, timeout=min(self.limits.connect_seconds, remaining)
        )
        try:
            connection.connect()
            if connection.sock is None:
                raise SourceError("GitHub connection has no socket")
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise SourceError("GitHub request deadline exceeded")
            connection.sock.settimeout(min(self.limits.read_seconds, remaining))
            headers = {"Accept": accept, "User-Agent": "orchestrated-codex"}
            if expected_host == "api.github.com":
                headers["X-GitHub-Api-Version"] = _API_VERSION
            connection.request("GET", parts.path, headers=headers)
            response = connection.getresponse()
            if self.clock() > deadline:
                raise SourceError("GitHub request deadline exceeded")
            if response.status != 200:
                raise _http_status_error(response)
            declared = response.getheader("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                raise SourceError("GitHub response exceeds size limit")
            read_socket = connection.sock
            if read_socket is None and response.fp is not None:
                read_socket = getattr(getattr(response.fp, "raw", None), "_sock", None)
            if read_socket is None:
                raise SourceError("GitHub response stream is unavailable")
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise SourceError("GitHub request deadline exceeded")
                read_socket.settimeout(min(self.limits.read_seconds, remaining))
                chunk = response.read(min(64 * 1024, max_bytes - size + 1))
                if self.clock() > deadline:
                    raise SourceError("GitHub request deadline exceeded")
                if not chunk:
                    return b"".join(chunks)
                size += len(chunk)
                if size > max_bytes:
                    raise SourceError("GitHub response exceeds size limit")
                chunks.append(chunk)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise SourceError(f"GitHub request failed: {exc}") from exc
        finally:
            connection.close()


def _sha(value: object, context: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise SourceError(f"{context} is not a full Git SHA")
    return value.lower()


def _path_component(value: str) -> str:
    if (
        not value
        or value in (".", "..")
        or "/" in value
        or "\\" in value
        or ":" in value
        or any(unicodedata.category(char) in ("Cc", "Cs") for char in value)
    ):
        raise SourceError(f"unsafe GitHub source path component: {value!r}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SourceError(f"GitHub source path is not valid UTF-8: {value!r}") from exc
    return value


def _source_path(path: Path, limits: SourceLimits) -> tuple[str, ...]:
    raw = path.as_posix()
    parts = tuple(_path_component(part) for part in raw.split("/"))
    if len(parts) > limits.path_components or len(raw.encode("utf-8")) > limits.path_bytes:
        raise SourceError(f"GitHub source path exceeds limit: {raw}")
    return parts


class GitHubSourceResolver:
    """Resolve one branch and fetch only explicitly requested files."""

    def __init__(
        self,
        fetch: Fetch | None = None,
        limits: SourceLimits = SourceLimits(),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = limits
        self.clock = clock
        self.fetch = fetch if fetch is not None else HTTPSFetcher(limits, clock)

    def _get(self, url: str, maximum: int, host: str, accept: str) -> bytes:
        deadline = self.clock() + self.limits.request_seconds
        data = self.fetch(url, maximum, host, deadline, accept)
        if self.clock() > deadline:
            raise SourceError("GitHub request deadline exceeded")
        if not isinstance(data, bytes) or len(data) > maximum:
            raise SourceError("GitHub response exceeds size limit")
        return data

    def resolve_ref(self, repository: GitHubRepository) -> str:
        ref = "/".join(quote(segment, safe="") for segment in repository.ref.split("/"))
        url = (
            f"https://api.github.com/repos/{repository.owner}/{repository.repository}"
            f"/git/ref/heads/{ref}"
        )
        data = self._get(url, self.limits.ref_bytes, "api.github.com", _JSON_ACCEPT)
        def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise SourceError(f"duplicate GitHub ref field: {key}")
                result[key] = value
            return result

        try:
            payload = json.loads(data, object_pairs_hook=unique_pairs)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise SourceError("invalid GitHub ref response") from exc
        if not isinstance(payload, dict) or payload.get("ref") != f"refs/heads/{repository.ref}":
            raise SourceError("GitHub ref response does not match configured branch")
        obj = payload.get("object")
        if not isinstance(obj, dict) or obj.get("type") != "commit":
            raise SourceError("GitHub ref does not point to a commit")
        return _sha(obj.get("sha"), "commit")

    def materialize(
        self,
        repository: GitHubRepository,
        paths: Iterable[Path],
        destination_root: Path,
    ) -> MaterializedRepository:
        requested: dict[Path, tuple[str, ...]] = {}
        seen: set[str] = set()
        for path in paths:
            parts = _source_path(Path(path), self.limits)
            key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
            if key in seen:
                raise SourceError(f"duplicate GitHub source path: {path}")
            seen.add(key)
            requested[Path(*parts)] = parts
        if not requested:
            return MaterializedRepository("", MappingProxyType({}))
        if len(requested) > self.limits.files:
            raise SourceError("too many selected GitHub files")
        for path in requested:
            if any(parent in requested for parent in path.parents):
                raise SourceError(f"GitHub source file is an ancestor of another file: {path}")
        skill_roots = {path.parent for path in requested if path.name == "SKILL.md"}
        if not skill_roots or any(
            not any(path.is_relative_to(root) for root in skill_roots)
            for path in requested
        ):
            raise SourceError("selected GitHub skill files must include SKILL.md")

        commit_sha = self.resolve_ref(repository)
        contents: dict[Path, bytes] = {}
        total = 0
        for path, parts in requested.items():
            encoded_path = "/".join(quote(part, safe="") for part in parts)
            url = (
                f"https://raw.githubusercontent.com/{repository.owner}/"
                f"{repository.repository}/{commit_sha}/{encoded_path}"
            )
            data = self._get(url, self.limits.file_bytes, "raw.githubusercontent.com", _RAW_ACCEPT)
            total += len(data)
            if total > self.limits.expanded_bytes:
                raise SourceError("selected GitHub files exceed aggregate size limit")
            contents[path] = data

        destination_root = Path(destination_root)
        if destination_root.is_symlink() or (destination_root.exists() and not destination_root.is_dir()):
            raise SourceError("materialization root must be a directory")
        try:
            destination_root.mkdir(parents=True, exist_ok=True)
            for path, data in contents.items():
                target = destination_root / path
                directory = destination_root
                for component in path.parts[:-1]:
                    directory /= component
                    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                        raise SourceError(f"unsafe materialization directory: {directory}")
                    directory.mkdir(exist_ok=True)
                with target.open("xb") as output:
                    output.write(data)
        except OSError as exc:
            raise SourceError(f"failed to materialize GitHub source: {exc}") from exc
        result = {path: destination_root / path for path in requested}
        return MaterializedRepository(commit_sha, MappingProxyType(result))
