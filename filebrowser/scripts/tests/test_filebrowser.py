from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from filebrowser_transfer.filebrowser import (
    FileBrowserClient,
    FileBrowserError,
    normalize_remote_path,
)
from filebrowser_transfer.models import FileBrowserSourceConfig, SecretValue


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


def test_falls_back_to_legacy_raw_download(tmp_path: Path) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/resources" and request.method == "GET":
            return httpx.Response(200, json={"type": "text/plain", "size": 3})
        if request.url.path == "/api/resources/download":
            return httpx.Response(405)
        if request.url.path == "/api/raw":
            assert request.url.params["files"] == "projects::/docs/a.txt"
            return httpx.Response(200, content=b"abc")
        return httpx.Response(404)

    output = tmp_path / "a.txt"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/docs/a.txt")
        client.download(remote, output)

    assert output.read_bytes() == b"abc"
    assert requested_paths[-2:] == ["/api/resources/download", "/api/raw"]


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


def test_upload_uses_sequential_chunks(tmp_path: Path) -> None:
    chunks: list[tuple[str, str, bytes]] = []
    local = tmp_path / "output.mp4"
    local.write_bytes(b"abcdefg")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/resources" and request.method == "GET":
            if not chunks:
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "video/mp4", "size": 7})
        if request.url.path == "/api/resources/download":
            return httpx.Response(200, headers={"content-length": "7"})
        chunks.append(
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

    assert chunks == [("0", "7", b"abc"), ("3", "7", b"def"), ("6", "7", b"g")]


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
