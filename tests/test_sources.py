"""Tests for commit-pinned, explicit GitHub file downloads."""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from codex_orchestrator import cli, sources
from codex_orchestrator.registry import GitHubRepository, GitHubSource
from codex_orchestrator.sources import GitHubSourceResolver, HTTPSFetcher, SourceError, SourceLimits


COMMIT = "a" * 40
SKILL = Path("skills/productivity/demo/SKILL.md")
AGENT = Path("skills/productivity/demo/agents/openai.yaml")


class _Socket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status, self.body, self.headers = status, body, headers or {}
        self.offset = 0
        self.fp = None

    def getheader(self, name: str) -> str | None:
        return self.headers.get(name)

    def read(self, amount: int) -> bytes:
        value = self.body[self.offset:self.offset + amount]
        self.offset += len(value)
        return value


class _Connection:
    responses: list[_Response] = []
    calls: list["_Connection"] = []

    def __init__(self, host: str, timeout: float) -> None:
        self.host, self.timeout, self.sock = host, timeout, _Socket()
        self.request_args: tuple[str, str, dict[str, str]] | None = None
        self.closed = False
        self.__class__.calls.append(self)

    def connect(self) -> None:
        pass

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        self.request_args = (method, path, headers)

    def getresponse(self) -> _Response:
        return self.__class__.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class SourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.destination = self.base / "materialized"
        self.repository = GitHubRepository(
            id="example-skills", owner="example", repository="skills", ref="release/2026.1",
            display_name="Example", licence="MIT",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def fetcher(
        self, files: dict[Path, bytes] | None = None, *,
        ref: bytes | None = None, fail_at: Path | None = None,
        calls: list[tuple[str, int, str, str]] | None = None,
        limits: SourceLimits = SourceLimits(),
    ) -> GitHubSourceResolver:
        files = files if files is not None else {SKILL: b"# Demo\n", AGENT: b"name: demo\n"}
        ref = ref if ref is not None else json.dumps({
            "ref": "refs/heads/release/2026.1", "object": {"type": "commit", "sha": COMMIT},
        }).encode()

        def fetch(url: str, maximum: int, host: str, deadline: float, accept: str) -> bytes:
            if calls is not None:
                calls.append((url, maximum, host, accept))
            if host == "api.github.com":
                return ref
            for path, data in files.items():
                if url.endswith("/" + path.as_posix()):
                    if path == fail_at:
                        raise SourceError("GitHub returned HTTP 404")
                    return data
            raise AssertionError(f"unexpected source request: {url}")

        return GitHubSourceResolver(fetch=fetch, limits=limits, clock=lambda: 0)

    def test_resolves_once_then_fetches_only_listed_files_at_that_commit(self) -> None:
        calls: list[tuple[str, int, str, str]] = []
        result = self.fetcher(calls=calls).materialize(
            self.repository, (SKILL, AGENT), self.destination
        )

        self.assertEqual(result.sha, COMMIT)
        self.assertEqual((self.destination / SKILL).read_bytes(), b"# Demo\n")
        self.assertEqual((self.destination / AGENT).read_bytes(), b"name: demo\n")
        self.assertEqual(set(result.paths), {SKILL, AGENT})
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][0], "https://api.github.com/repos/example/skills/git/ref/heads/release/2026.1")
        self.assertEqual(calls[0][2:], ("api.github.com", "application/vnd.github+json"))
        self.assertEqual(
            {call[0] for call in calls[1:]},
            {f"https://raw.githubusercontent.com/example/skills/{COMMIT}/{path}" for path in (SKILL, AGENT)},
        )
        self.assertTrue(all(call[2:] == ("raw.githubusercontent.com", "application/octet-stream") for call in calls[1:]))
        self.assertFalse(any("/git/trees/" in call[0] or "/git/blobs/" in call[0] for call in calls))

    def test_shipped_all_remote_selection_is_one_ref_plus_twelve_files(self) -> None:
        registry = cli.default_registry()
        remote_paths = {
            resource.source.path
            for component in registry.components
            for resource in component.resources
            if isinstance(resource.source, GitHubSource)
        }
        self.assertEqual(len(remote_paths), 12)
        calls: list[tuple[str, int, str, str]] = []
        files = {path: b"# skill\n" for path in remote_paths}
        repo = registry.repositories["mattpocock-skills"]
        ref = json.dumps({"ref": "refs/heads/main", "object": {"type": "commit", "sha": COMMIT}}).encode()
        result = self.fetcher(files, ref=ref, calls=calls).materialize(repo, remote_paths, self.destination)
        self.assertEqual(result.sha, COMMIT)
        self.assertEqual(len(calls), 13)
        self.assertEqual(sum(call[2] == "api.github.com" for call in calls), 1)
        self.assertEqual(sum(call[2] == "raw.githubusercontent.com" for call in calls), 12)

    def test_all_downloads_finish_before_materialization(self) -> None:
        calls: list[tuple[str, int, str, str]] = []
        resolver = self.fetcher(calls=calls, fail_at=AGENT)
        with self.assertRaisesRegex(SourceError, "HTTP 404"):
            resolver.materialize(self.repository, (SKILL, AGENT), self.destination)
        self.assertEqual(len(calls), 3)
        self.assertFalse(self.destination.exists())

    def test_invalid_ref_and_requested_paths_fail_without_writes(self) -> None:
        invalid_refs = (
            b"not-json", b"{}",
            json.dumps({"ref": "refs/heads/other", "object": {"type": "commit", "sha": COMMIT}}).encode(),
            json.dumps({"ref": "refs/heads/release/2026.1", "object": {"type": "tag", "sha": COMMIT}}).encode(),
            json.dumps({"ref": "refs/heads/release/2026.1", "object": {"type": "commit", "sha": "short"}}).encode(),
            b'{"ref":"refs/heads/release/2026.1","ref":"refs/heads/release/2026.1"}',
        )
        for index, ref in enumerate(invalid_refs):
            with self.subTest(ref=index), self.assertRaises(SourceError):
                self.fetcher(ref=ref).materialize(self.repository, (SKILL,), self.base / str(index))
            self.assertFalse((self.base / str(index)).exists())
        for paths in ((AGENT,), (SKILL, SKILL), (SKILL, Path("../outside"))):
            with self.subTest(paths=paths), self.assertRaises(SourceError):
                self.fetcher().materialize(self.repository, paths, self.destination)
            self.assertFalse(self.destination.exists())

    def test_bounded_downloads_and_request_deadline(self) -> None:
        files = {SKILL: b"1234", AGENT: b"5678"}
        self.fetcher(files, limits=replace(SourceLimits(), file_bytes=4, expanded_bytes=8)).materialize(
            self.repository, (SKILL, AGENT), self.base / "at-limit"
        )
        for limits, expected in (
            (replace(SourceLimits(), file_bytes=3), "size limit"),
            (replace(SourceLimits(), expanded_bytes=7), "aggregate"),
            (replace(SourceLimits(), files=1), "too many"),
            (replace(SourceLimits(), path_components=3), "path exceeds"),
            (replace(SourceLimits(), path_bytes=len(SKILL.as_posix().encode()) - 1), "path exceeds"),
            (replace(SourceLimits(), ref_bytes=4), "size limit"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(SourceError, expected):
                self.fetcher(files, limits=limits).materialize(self.repository, (SKILL, AGENT), self.base / expected)
        ticks = iter((0, 2, 3))
        expired = GitHubSourceResolver(fetch=lambda *_: b"{}", limits=replace(SourceLimits(), request_seconds=1), clock=lambda: next(ticks))
        with self.assertRaisesRegex(SourceError, "deadline"):
            expired.resolve_ref(self.repository)

    def test_https_fetcher_hosts_headers_status_and_local_reset(self) -> None:
        _Connection.calls = []
        _Connection.responses = [_Response(200, b"ok", {"Content-Length": "2"})]
        with unittest.mock.patch.object(sources.http.client, "HTTPSConnection", _Connection):
            self.assertEqual(HTTPSFetcher(clock=lambda: 0)(
                "https://raw.githubusercontent.com/example/skills/sha/SKILL.md", 2,
                "raw.githubusercontent.com", 5, "application/octet-stream",
            ), b"ok")
        self.assertEqual(_Connection.calls[0].request_args[2]["User-Agent"], "orchestrated-codex")
        self.assertNotIn("X-GitHub-Api-Version", _Connection.calls[0].request_args[2])

        _Connection.responses = [_Response(200, b"{}")]
        with unittest.mock.patch.object(sources.http.client, "HTTPSConnection", _Connection):
            HTTPSFetcher(clock=lambda: 0)("https://api.github.com/path", 2, "api.github.com", 5)
        self.assertEqual(_Connection.calls[-1].request_args[2]["X-GitHub-Api-Version"], "2022-11-28")

        _Connection.responses = [_Response(403, b"", {
            "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000",
        })]
        with unittest.mock.patch.object(sources.http.client, "HTTPSConnection", _Connection):
            with self.assertRaises(SourceError) as caught:
                HTTPSFetcher(clock=lambda: 0)("https://api.github.com/path", 2, "api.github.com", 5)
        reset = datetime.fromtimestamp(2000000000, timezone.utc).astimezone()
        self.assertIn(f"{reset:%Y-%m-%d %H:%M %Z} (local time)", str(caught.exception))

        _Connection.responses = [_Response(302, b"")]
        with unittest.mock.patch.object(sources.http.client, "HTTPSConnection", _Connection):
            with self.assertRaisesRegex(SourceError, "HTTP 302"):
                HTTPSFetcher(clock=lambda: 0)("https://api.github.com/path", 2, "api.github.com", 5)
        with self.assertRaisesRegex(SourceError, "unexpected GitHub request URL"):
            HTTPSFetcher(clock=lambda: 0)("https://other.example/path", 2, "other.example", 5)
