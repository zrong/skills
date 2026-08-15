from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture

from filebrowser_transfer.cli import main
from filebrowser_transfer.filebrowser import FileBrowserError
from filebrowser_transfer.models import (
    FileBrowserSourceConfig,
    RemoteFile,
    SecretValue,
    SkillConfig,
    UnchangedFile,
)
from filebrowser_transfer.transfer import TransferService


class FakeSource:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> FakeSource:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.closed = True

    def metadata(self, remote_path: str) -> RemoteFile:
        return RemoteFile("projects", remote_path, "video.mp4", 5, "video/mp4")

    def download(self, remote: RemoteFile, destination: Path) -> int:
        del remote
        destination.write_bytes(b"video")
        return 5

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        overwrite: bool = False,
    ) -> RemoteFile:
        assert local_path.read_bytes() == b"video"
        assert remote_path == "/shows/demo/output.mp4"
        assert overwrite is False
        return RemoteFile("projects", remote_path, "output.mp4", 5, "video/mp4")


class FakeObjectStorage:
    def __init__(self, *, skipped: bool = False) -> None:
        self.skipped = skipped
        self.local_existed_during_upload = False
        self.upload_options: dict[str, object] = {}
        self.cdn_options: dict[str, object] = {}

    def summary(self) -> dict[str, object]:
        return {"config_path": "/config", "default_target": "archive", "targets": []}

    def resolve_key(self, key: str, *, target_name: str | None = None) -> dict[str, object]:
        return {
            "target_name": target_name or "archive",
            "object_key": f"backup/{key}",
        }

    def upload(
        self,
        local_path: Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
        content_type: str = "",
    ) -> dict[str, object]:
        self.local_existed_during_upload = local_path.exists()
        self.upload_options = {
            "target_name": target_name,
            "object_key": object_key,
            "overwrite": overwrite,
            "if_changed": if_changed,
            "content_type": content_type,
        }
        return {
            "target_name": target_name or "archive",
            "bucket": "bucket",
            "object_key": f"backup/{object_key}",
            "size": 5,
            "etag": "etag",
            "version_id": "",
            "public_url": "",
            "cdn_task": None,
            "skipped_unchanged": self.skipped,
            "content_sha256": "sha256-value",
            "unchanged_files": [],
        }

    def cdn(
        self,
        command: str,
        *,
        target_name: str | None,
        urls: list[str] | None,
        keys: list[str] | None,
        flush_type: str = "flush",
        area: str = "",
        dry_run: bool = False,
    ) -> dict[str, object]:
        self.cdn_options = {
            "command": command,
            "target_name": target_name,
            "urls": urls,
            "keys": keys,
            "flush_type": flush_type,
            "area": area,
            "dry_run": dry_run,
        }
        return {
            "operation": command.replace("-", "_"),
            "status": "submitted",
            "task_id": "task-1",
            "targets": urls or keys or [],
        }


def _skill_config(staging_dir: Path | None = None) -> SkillConfig:
    return SkillConfig(
        sources={
            "main": FileBrowserSourceConfig(
                name="main",
                base_url="https://files.example.test",
                token=SecretValue(direct="secret"),
                source="projects",
            )
        },
        default_source="main",
        staging_dir=staging_dir,
    )


def _service(
    source: FakeSource,
    storage: FakeObjectStorage,
    staging_dir: Path | None = None,
) -> TransferService:
    return TransferService(
        _skill_config(staging_dir),
        source_factory=lambda _config: source,
        object_storage_factory=lambda _path: storage,
    )


def test_transfer_delegates_and_cleans_staging(tmp_path: Path) -> None:
    source = FakeSource()
    storage = FakeObjectStorage()
    result = _service(source, storage, tmp_path).upload("/shows/demo/video.mp4")

    assert result.object_key == "backup/shows/demo/video.mp4"
    assert storage.local_existed_during_upload is True
    assert storage.upload_options["content_type"] == "video/mp4"
    assert source.closed is True
    assert list(tmp_path.iterdir()) == []


def test_transfer_reports_explicit_unchanged_file_list() -> None:
    source = FakeSource()
    storage = FakeObjectStorage(skipped=True)
    result = _service(source, storage).upload(
        "/shows/demo/video.mp4", overwrite=True, if_changed=True
    )

    assert storage.upload_options["overwrite"] is True
    assert storage.upload_options["if_changed"] is True
    assert result.unchanged_files == [
        UnchangedFile(
            source_path="/shows/demo/video.mp4",
            object_key="backup/shows/demo/video.mp4",
            size=5,
            content_sha256="sha256-value",
        )
    ]


def test_transfer_rejects_if_changed_without_overwrite() -> None:
    service = _service(FakeSource(), FakeObjectStorage())
    with pytest.raises(FileBrowserError, match="requires --overwrite"):
        service.upload("/shows/demo/video.mp4", if_changed=True)


def test_put_and_get_still_use_filebrowser(tmp_path: Path) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"video")
    source = FakeSource()
    service = _service(source, FakeObjectStorage())
    put_result = service.put(local, "/shows/demo/output.mp4")
    downloaded = tmp_path / "downloaded.mp4"
    get_result = service.get("/shows/demo/video.mp4", downloaded)

    assert put_result.size == 5
    assert get_result.size == 5
    assert downloaded.read_bytes() == b"video"


def _write_config(path: Path) -> None:
    path.write_text(
        """
[filebrowser]
default_source = "main"

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "MISSING_TOKEN_IS_OK_FOR_DRY_RUN"
source = "projects"

[object-storage]
default_target = "archive"

[object-storage.targets.archive]
adapter = "s3"
bucket = "bucket"
prefix = "backup"
access_key_id_env = "MISSING_ACCESS_IS_OK_FOR_DRY_RUN"
secret_access_key_env = "MISSING_SECRET_IS_OK_FOR_DRY_RUN"

[object-storage.targets.archive.cdn]
provider = "tencent"
base_url = "https://cdn.example.test"
""".strip(),
        encoding="utf-8",
    )


def test_cli_upload_dry_run_delegates_to_object_storage(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "agent_config.toml"
    _write_config(config_path)
    result = main(
        [
            "--config",
            str(config_path),
            "--non-interactive",
            "upload",
            "/shows/demo/video.mp4",
            "--dry-run",
            "--overwrite",
            "--if-changed",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["object_key"] == "backup/shows/demo/video.mp4"
    assert payload["if_changed"] is True


def test_cli_put_dry_run_needs_only_filebrowser_section(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"video")
    config_path = tmp_path / "agent_config.toml"
    _write_config(config_path)
    result = main(
        [
            "--config",
            str(config_path),
            "put",
            str(local),
            "/shows/demo/output.mp4",
            "--dry-run",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["size"] == 5


def test_cli_cdn_purge_url_dry_run_delegates(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "agent_config.toml"
    _write_config(config_path)
    result = main(
        [
            "--config",
            str(config_path),
            "cdn",
            "purge-url",
            "--target",
            "archive",
            "--keys",
            "a/b.mp4",
            "--dry-run",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["targets"] == ["https://cdn.example.test/a/b.mp4"]
    assert payload["dry_run"] is True
