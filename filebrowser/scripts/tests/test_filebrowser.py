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


def _config(*, max_bytes: int = 0) -> FileBrowserSourceConfig:
    return FileBrowserSourceConfig(
        name="main",
        base_url="https://files.example.test",
        token=SecretValue(direct="secret-token"),
        source="projects",
        max_transfer_bytes=max_bytes,
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
        if request.url.path == "/api/resources":
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
        if request.url.path == "/api/resources":
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
        return httpx.Response(200, content=b"bad")

    output = tmp_path / "bad.txt"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.metadata("/bad.txt")
        with pytest.raises(FileBrowserError, match="size mismatch"):
            client.download(remote, output)
    assert not output.exists()
    assert not output.with_suffix(".txt.part").exists()
