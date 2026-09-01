from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from filebrowser_transfer.cli import build_parser
from filebrowser_transfer.config import SkillConfig
from filebrowser_transfer.filebrowser import FileBrowserClient, FileBrowserError
from filebrowser_transfer.models import (
    FileBrowserSourceConfig,
    SecretValue,
)
from filebrowser_transfer.transfer import TransferService


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


def test_info_returns_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["source"] == "projects"
        assert request.url.params["path"] == "/docs/readme.md"
        assert request.url.params["content"] == "true"
        return httpx.Response(
            200,
            json={"type": "text/markdown", "size": 42, "content": "hello"},
        )

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        payload = client.info("/docs/readme.md", content=True)

    assert payload == {"type": "text/markdown", "size": 42, "content": "hello"}


def test_exists_true_and_false() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"type": "directory"}),
            httpx.Response(404),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        assert client.exists("/present")
        assert not client.exists("/absent")


def test_list_files_returns_children() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "directory",
                "files": [
                    {"name": "a.txt", "type": "text/plain", "size": 1},
                    "not-a-dict",
                ],
            },
        )

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        items = client.list_files("/docs")

    assert [item["name"] for item in items] == ["a.txt"]


def test_list_files_merges_folders_and_files() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "directory",
                "folders": [{"name": "sub/", "type": "directory"}],
                "files": [{"name": "a.txt", "type": "text/plain", "size": 1}],
            },
        )

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        items = client.list_files("/docs")

    assert [item["name"] for item in items] == ["sub/", "a.txt"]


def test_list_files_allows_root_directory() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["path"] == "/"
        return httpx.Response(200, json={"type": "directory", "files": []})

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        assert client.list_files("/") == []


def test_ensure_dir_creates_when_missing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        assert request.url.params["isDir"] == "true"
        return httpx.Response(200)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        result = client.ensure_dir("/new/dir/")

    assert result == "/new/dir"
    assert requests[0].method == "GET"
    assert requests[1].method == "POST"


def test_ensure_dir_accepts_concurrent_create_conflict() -> None:
    responses = iter(
        [
            httpx.Response(404),
            httpx.Response(409),
            httpx.Response(200, json={"type": "directory"}),
        ]
    )

    with FileBrowserClient(
        _config(), transport=httpx.MockTransport(lambda _: next(responses))
    ) as client:
        assert client.ensure_dir("/new/dir") == "/new/dir"


def test_ensure_dir_returns_existing_directory() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "directory"})

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        assert client.ensure_dir("/already/here") == "/already/here"


def test_ensure_dir_rejects_existing_file() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "text/plain"})

    with (
        FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(FileBrowserError, match="not a directory"),
    ):
        client.ensure_dir("/regular.txt")


def test_update_file_puts_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"type": "text/plain"})
        captured["method"] = request.method
        captured["path"] = request.url.params["path"]
        captured["body"] = request.content
        captured["override"] = request.url.params.get("override")
        return httpx.Response(200)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        client.update_file("/notes/today.md", b"# today", overwrite=True)

    assert captured == {
        "method": "PUT",
        "path": "/notes/today.md",
        "body": b"# today",
        "override": None,
    }


def test_update_file_refuses_existing_file_without_override() -> None:
    with (
        FileBrowserClient(
            _config(),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"type": "text/plain"})
            ),
        ) as client,
        pytest.raises(FileBrowserError, match="pass --override"),
    ):
        client.update_file("/notes/today.md", b"# today")


def test_update_parser_accepts_override() -> None:
    args = build_parser().parse_args(["update", "--path", "/notes/today.md", "--override"])
    assert args.override is True


def test_delete_sends_correct_path() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.params["path"]
        return httpx.Response(200)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        client.delete("/docs/old.txt")

    assert captured == {"method": "DELETE", "path": "/docs/old.txt"}


def test_move_rename_sends_quantum_json_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # destination pre-check
            return httpx.Response(404)
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"succeeded": [{"fromPath": "/old.txt"}], "failed": []})

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        client.move("/old.txt", "/new.txt", action="rename", overwrite=True)

    assert captured == {
        "method": "PATCH",
        "path": "/api/resources",
        "body": {
            "items": [
                {
                    "fromSource": "projects",
                    "fromPath": "/old.txt",
                    "toSource": "projects",
                    "toPath": "/new.txt",
                }
            ],
            "action": "rename",
            "overwrite": True,
        },
    }


def test_move_copy_sends_quantum_json_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # destination pre-check
            return httpx.Response(404)
        captured["method"] = request.method
        captured["query_from"] = request.url.params.get("from")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"succeeded": [], "failed": []})

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        client.move("/a/b.mp4", "/c/b.mp4", action="copy")

    assert captured["method"] == "PATCH"
    assert captured["query_from"] is None
    assert captured["body"] == {
        "items": [
            {
                "fromSource": "projects",
                "fromPath": "/a/b.mp4",
                "toSource": "projects",
                "toPath": "/c/b.mp4",
            }
        ],
        "action": "copy",
        "overwrite": False,
    }


def test_move_refuses_existing_destination_without_overwrite() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(200, json={"type": "text/plain"})

    with (
        FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(FileBrowserError, match="pass --overwrite"),
    ):
        client.move("/old.txt", "/new.txt")

    assert requests == ["GET"]


def test_move_allows_existing_destination_with_overwrite() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"type": "text/plain"})
        return httpx.Response(200, json={"succeeded": [], "failed": []})

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        client.move("/old.txt", "/new.txt", overwrite=True)


def test_move_raises_when_item_reports_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # destination pre-check
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={"failed": [{"fromPath": "/old.txt", "message": "destination exists"}]},
        )

    with (
        FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(FileBrowserError, match="destination exists"),
    ):
        client.move("/old.txt", "/new.txt")


def test_move_rejects_identical_paths() -> None:
    with (
        FileBrowserClient(
            _config(), transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client,
        pytest.raises(FileBrowserError, match="identical"),
    ):
        client.move("/a.txt", "/a.txt")


def test_search_with_scope() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = request.url.params["query"]
        captured["sources"] = request.url.params["sources"]
        captured["scope"] = request.url.params.get("scope", "")
        return httpx.Response(200, json=[{"path": "/pictures/a.jpg", "name": "a.jpg"}])

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        results = client.search("photo", scope="/pictures")

    assert captured == {
        "path": "/api/tools/search",
        "query": "photo",
        "sources": "projects",
        "scope": "projects:/pictures",
    }
    assert results == [{"path": "/pictures/a.jpg", "name": "a.jpg"}]


def test_preview_writes_to_output(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/resources/preview"
        assert request.url.params["source"] == "projects"
        assert request.url.params["path"] == "/photos/hero.jpg"
        assert request.url.params["size"] == "large"
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nbinary")

    destination = tmp_path / "preview.png"
    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        result = client.preview("/photos/hero.jpg", size="large", output=str(destination))

    assert result == destination
    assert destination.read_bytes() == b"\x89PNG\r\n\x1a\nbinary"


def test_list_sources_returns_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/settings"
        assert request.url.params["property"] == "sources"
        return httpx.Response(
            200,
            json=[{"name": "projects"}, {"name": "archive"}, "skip"],
        )

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        items = client.list_sources()

    assert [item["name"] for item in items] == ["projects", "archive"]


def test_upload_file_to_dir_creates_then_uploads(tmp_path: Path) -> None:
    local = tmp_path / "clip.mp4"
    local.write_bytes(b"clip-bytes")
    state = {"info_dir": 0, "clip_metadata": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.params.get("path") == "/remote/成片":
            state["info_dir"] += 1
            return httpx.Response(404)
        if request.method == "POST" and request.url.params.get("isDir") == "true":
            return httpx.Response(200)
        if request.method == "POST" and request.url.params.get("path", "").endswith("/clip.mp4"):
            return httpx.Response(200)
        if request.method == "GET" and request.url.params.get("path", "").endswith("/clip.mp4"):
            state["clip_metadata"] += 1
            # First GET = destination check (must 404); second GET = post-upload metadata
            if state["clip_metadata"] == 1:
                return httpx.Response(404)
            return httpx.Response(200, json={"type": "video/mp4", "size": 10})
        if request.method == "GET" and request.url.path == "/api/resources/download":
            return httpx.Response(200, headers={"content-length": "10"})
        return httpx.Response(404)

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        remote = client.upload_file_to_dir(local, "/remote/成片", remote_name="clip.mp4")

    assert remote.path == "/remote/成片/clip.mp4"
    assert state["info_dir"] == 1
    assert state["clip_metadata"] == 2


def test_download_files_uses_repeated_file_params() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = request.url.params.multi_items()
        return httpx.Response(200, content=b"PK\x03\x04zip")

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        path = client.download_files(["main::/a.txt", "/b.txt"], algo="zip")

    assert captured["path"] == "/api/resources/download"
    assert captured["params"] == [
        ("file", "/a.txt"),
        ("file", "/b.txt"),
        ("algo", "zip"),
        ("source", "projects"),
    ]
    assert path.read_bytes() == b"PK\x03\x04zip"


def test_download_files_falls_back_to_raw_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/resources/download":
            return httpx.Response(404)
        return httpx.Response(200, content=b"PK\x03\x04zip")

    with FileBrowserClient(_config(), transport=httpx.MockTransport(handler)) as client:
        path = client.download_files(["/a.txt"], algo="zip")

    assert seen == ["/api/resources/download", "/api/raw"]
    assert path.read_bytes() == b"PK\x03\x04zip"


def test_download_files_rejects_foreign_source_prefix() -> None:
    with (
        FileBrowserClient(
            _config(), transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client,
        pytest.raises(FileBrowserError, match="does not match"),
    ):
        client.download_files(["staging::/a.txt"])


def test_download_files_rejects_unsupported_algo() -> None:
    with (
        FileBrowserClient(
            _config(), transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client,
        pytest.raises(FileBrowserError, match="zip or tar.gz"),
    ):
        client.download_files(["/a.txt"], algo="tar")


def test_download_files_requires_at_least_one_path() -> None:
    with (
        FileBrowserClient(
            _config(), transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client,
        pytest.raises(FileBrowserError, match="at least one"),
    ):
        client.download_files([])


# ----------------------------------------------------------------------
# TransferService multi-source routing
# ----------------------------------------------------------------------


def _skill_config_with_two_sources() -> SkillConfig:
    return SkillConfig(
        default_source="main",
        sources={
            "main": _config(),
            "staging": FileBrowserSourceConfig(
                name="staging",
                base_url="https://staging.example.test",
                token=SecretValue(direct="stage-token"),
                source="drafts",
            ),
        },
    )


def test_service_routes_info_to_requested_source() -> None:
    seen_bases: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bases.append(str(request.url))
        if "staging" in str(request.url):
            return httpx.Response(200, json={"type": "directory", "staging": True})
        return httpx.Response(200, json={"type": "directory", "main": True})

    config = _skill_config_with_two_sources()
    service = TransferService(
        config,
        source_factory=lambda cfg: FileBrowserClient(cfg, transport=httpx.MockTransport(handler)),
    )
    payload_main = service.info("/path", source_name="main")
    payload_staging = service.info("/path", source_name="staging")
    assert payload_main == {"type": "directory", "main": True}
    assert payload_staging == {"type": "directory", "staging": True}
    assert any("files.example.test" in url for url in seen_bases)
    assert any("staging.example.test" in url for url in seen_bases)


def test_service_uses_default_source_when_omitted() -> None:
    config = _skill_config_with_two_sources()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"type": "directory"})

    service = TransferService(
        config,
        source_factory=lambda cfg: FileBrowserClient(cfg, transport=httpx.MockTransport(handler)),
    )
    service.info("/path")
    assert seen == ["Bearer secret-token"]
