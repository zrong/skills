from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from filebrowser_transfer.filebrowser import (
    FileBrowserClient,
    FileBrowserError,
    normalize_remote_path,
)
from filebrowser_transfer.models import ConfigurationError, FileBrowserSourceConfig, SecretValue


def _config(
    *,
    max_bytes: int = 0,
    upload_chunk_bytes: int = 16 * 1024 * 1024,
) -> FileBrowserSourceConfig:
    return FileBrowserSourceConfig(
        name="main",
        base_url="https://files.example.test",
        token=SecretValue(direct="secret-token"),
        source="projects",
        max_transfer_bytes=max_bytes,
        upload_chunk_bytes=upload_chunk_bytes,
    )


def test_normalize_remote_path() -> None:
    assert normalize_remote_path("projects/video.mp4") == "/projects/video.mp4"
    assert normalize_remote_path("/projects/./video.mp4") == "/projects/video.mp4"
    with pytest.raises(FileBrowserError):
        normalize_remote_path("/projects/../secret.txt")


def test_modern_metadata_and_stream_download(tmp_path: Path) -> None:
    seen_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_authorization
        seen_authorization = request.headers.get("authorization", "")
        if request.url.path == "/api/resources" and request.method == "GET":
            return httpx.Response(
                200,
                json={"type": "video/mp4", "size": 7},
            )
        if request.url.path == "/api/resources/download":
            return httpx.Response(
                200,
                content=b"content",
                headers={"content-type": "video/mp4", "content-length": "7"},
            )
        return httpx.Response(404)

    output = tmp_path / "video.mp4"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/media/video.mp4")
        written = client.download(remote, output)

    assert written == 7
    assert output.read_bytes() == b"content"
    assert remote.content_type == "video/mp4"
    assert seen_authorization == "Bearer secret-token"


@pytest.mark.parametrize("status", [404, 405])
def test_download_propagates_error_without_retry(tmp_path: Path, status: int) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/resources":
            return httpx.Response(200, json={"type": "text/plain", "size": 3})
        return httpx.Response(status)

    output = tmp_path / "a.txt"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/docs/a.txt")
        with pytest.raises(FileBrowserError):
            client.download(remote, output)

    assert requested_paths == ["/api/resources", "/api/resources/download"]
    assert not output.exists()
    assert not output.with_suffix(".txt.part").exists()


def test_rejects_directory_and_oversized_file() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"type": "directory"}),
            httpx.Response(200, json={"type": "video/mp4", "size": 11}),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    with FileBrowserClient(_config(max_bytes=10), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(FileBrowserError, match="directories"):
            client.metadata("/folder")
        with pytest.raises(FileBrowserError, match="max_transfer_bytes"):
            client.metadata("/large.mp4")


def test_size_mismatch_removes_partial_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/resources":
            return httpx.Response(200, json={"type": "text/plain", "size": 5})
        return httpx.Response(200, content=b"bad", headers={"content-length": "4"})

    output = tmp_path / "bad.txt"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/bad.txt")
        with pytest.raises(FileBrowserError, match="size mismatch"):
            client.download(remote, output)
    assert not output.exists()
    assert not output.with_suffix(".txt.part").exists()


def test_download_uses_response_content_length_when_metadata_is_stale(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/resources":
            return httpx.Response(200, json={"type": "video/mp4", "size": 8})
        return httpx.Response(200, content=b"content", headers={"content-length": "7"})

    output = tmp_path / "video.mp4"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/video.mp4")
        written = client.download(remote, output)

    assert written == 7
    assert output.read_bytes() == b"content"


def test_upload_streams_file_and_verifies_remote_size(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    local = tmp_path / "output.mp4"
    local.write_bytes(b"branded")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/resources" and request.method == "GET":
            if len([item for item in requests if item.method == "GET"]) == 1:
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "video/mp4", "size": 7})
        if request.url.path == "/api/resources/download":
            return httpx.Response(200, headers={"content-length": "7"})
        assert request.method == "POST"
        assert request.content == b"branded"
        return httpx.Response(200)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.upload_file(local, "/shows/output.mp4")

    assert remote.size == 7
    post = next(request for request in requests if request.method == "POST")
    assert post.url.path == "/api/resources"
    assert post.url.params["source"] == "projects"
    assert post.url.params["path"] == "/shows/output.mp4"
    assert post.url.params["override"] == "false"
    assert post.headers["authorization"] == "Bearer secret-token"


def test_upload_rejects_existing_destination_without_overwrite(tmp_path: Path) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"video")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json={"type": "video/mp4", "size": 5})

    with (
        FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(FileBrowserError, match="already exists"),
    ):
        client.upload_file(local, "/shows/output.mp4")


def test_upload_uses_sequential_chunks_with_per_file_session(tmp_path: Path) -> None:
    chunks: dict[str, list[tuple[str, str, bytes]]] = {}
    sessions: dict[str, set[str]] = {}
    metadata_requests: dict[str, int] = {}
    local = tmp_path / "output.mp4"
    local.write_bytes(b"abcdefg")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/resources" and request.method == "GET":
            path = request.url.params["path"]
            metadata_requests[path] = metadata_requests.get(path, 0) + 1
            if metadata_requests[path] == 1:
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "video/mp4", "size": 7})
        if request.url.path == "/api/resources/download":
            return httpx.Response(200, headers={"content-length": "7"})
        assert request.method == "POST"
        session = request.headers.get("x-file-upload-session", "")
        if not session:
            return httpx.Response(400)
        assert len(session) <= 128
        assert all(
            c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in session
        )
        path = request.url.params["path"]
        sessions.setdefault(path, set()).add(session)
        chunks.setdefault(path, []).append(
            (
                request.headers["x-file-chunk-offset"],
                request.headers["x-file-total-size"],
                request.content,
            )
        )
        return httpx.Response(200)

    with FileBrowserClient(
        _config(upload_chunk_bytes=3), transport=httpx.MockTransport(handler)
    ) as client:
        client.upload_file(local, "/shows/output.mp4")
        client.upload_file(local, "/shows/another.mp4")

    expected = [("0", "7", b"abc"), ("3", "7", b"def"), ("6", "7", b"g")]
    assert chunks == {"/shows/output.mp4": expected, "/shows/another.mp4": expected}
    assert all(len(ids) == 1 for ids in sessions.values())
    assert sessions["/shows/output.mp4"].isdisjoint(sessions["/shows/another.mp4"])


def test_upload_prefers_download_response_size_over_stale_metadata(tmp_path: Path) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"content")
    metadata_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_requests
        if request.url.path == "/api/resources":
            metadata_requests += 1
            if metadata_requests == 1:
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "video/mp4", "size": 8})
        if request.url.path == "/api/resources/download":
            return httpx.Response(200, headers={"content-length": "7"})
        return httpx.Response(200)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.upload_file(local, "/shows/output.mp4")

    assert remote.size == 7


def test_empty_token_fails_before_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    config = FileBrowserSourceConfig(
        name="main", base_url="https://files.example.test", token=SecretValue()
    )
    with pytest.raises(ConfigurationError, match="Missing FileBrowser API key"):
        FileBrowserClient(config, transport=httpx.MockTransport(handler))
    assert not requests


def test_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_FILEBROWSER_KEY", "environment-api-key")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/resources"
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"type": "directory"})

    config = FileBrowserSourceConfig(
        name="main",
        base_url="https://files.example.test",
        token=SecretValue(env_var="REVIEW_FILEBROWSER_KEY"),
    )
    with FileBrowserClient(config, transport=httpx.MockTransport(handler)) as client:
        client.info("/")
    assert seen == ["Bearer environment-api-key"]


@pytest.mark.parametrize("status", [404, 405])
def test_upload_verification_propagates_error_without_retry(tmp_path: Path, status: int) -> None:
    seen: list[str] = []
    local = tmp_path / "a.txt"
    local.write_bytes(b"abc")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(200)
        if len(seen) == 1:
            return httpx.Response(404)
        if request.url.path == "/api/resources":
            return httpx.Response(200, json={"type": "text/plain", "size": 3})
        return httpx.Response(status)

    with (
        FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(FileBrowserError),
    ):
        client.upload_file(local, "/a.txt")
    assert seen == ["/api/resources"] * 3 + ["/api/resources/download"]
