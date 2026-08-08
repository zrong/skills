from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from filebrowser_transfer.cli import main
from filebrowser_transfer.models import (
    CdnTargetConfig,
    CdnTaskResult,
    FileBrowserSourceConfig,
    RemoteFile,
    S3TargetConfig,
    SecretValue,
    SkillConfig,
    UploadResult,
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


class FakeTarget:
    name = "archive"

    def __init__(self) -> None:
        self.local_existed_during_upload = False
        self.preflight: tuple[str, bool] | None = None

    def resolve_key(self, relative_key: str) -> str:
        return f"backup/{relative_key}"

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> None:
        self.preflight = (object_key, overwrite)

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str,
    ) -> UploadResult:
        assert content_type == "video/mp4"
        self.local_existed_during_upload = local_path.exists()
        return UploadResult("archive", "bucket", object_key, 5, "etag", "", "")


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
        targets={"archive": S3TargetConfig(name="archive", bucket="bucket", prefix="backup")},
        default_source="main",
        default_target="archive",
        staging_dir=staging_dir,
    )


def test_transfer_preserves_remote_path_and_cleans_staging(tmp_path: Path) -> None:
    source = FakeSource()
    target = FakeTarget()
    service = TransferService(
        _skill_config(tmp_path),
        source_factory=lambda _config: source,
        target_factory=lambda _config: target,
    )
    result = service.upload("/shows/demo/video.mp4")

    assert result.object_key == "backup/shows/demo/video.mp4"
    assert target.preflight == ("backup/shows/demo/video.mp4", False)
    assert target.local_existed_during_upload is True
    assert source.closed is True
    assert list(tmp_path.iterdir()) == []


def test_cli_dry_run_uses_multiple_target_config_without_credentials(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]
default_source = "main"
default_target = "archive"

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "MISSING_TOKEN_IS_OK_FOR_DRY_RUN"
source = "projects"

[filebrowser.targets.archive]
adapter = "s3"
bucket = "bucket"
prefix = "backup"
access_key_id_env = "MISSING_ACCESS_IS_OK_FOR_DRY_RUN"
secret_access_key_env = "MISSING_SECRET_IS_OK_FOR_DRY_RUN"
""",
        encoding="utf-8",
    )
    result = main(
        [
            "--config",
            str(config_path),
            "--non-interactive",
            "upload",
            "/shows/demo/video.mp4",
            "--dry-run",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["object_key"] == "backup/shows/demo/video.mp4"
    assert payload["dry_run"] is True


def test_put_uploads_to_filebrowser_and_checks_reported_size(tmp_path: Path) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"video")
    source = FakeSource()
    service = TransferService(
        _skill_config(),
        source_factory=lambda _config: source,
    )

    result = service.put(local, "/shows/demo/output.mp4")

    assert result.source_name == "main"
    assert result.remote_path == "/shows/demo/output.mp4"
    assert result.size == 5
    assert source.closed is True


def test_get_downloads_filebrowser_file_to_new_local_path(tmp_path: Path) -> None:
    source = FakeSource()
    service = TransferService(
        _skill_config(),
        source_factory=lambda _config: source,
    )
    local = tmp_path / "downloaded.mp4"

    result = service.get("/shows/demo/video.mp4", local)

    assert local.read_bytes() == b"video"
    assert result.remote_path == "/shows/demo/video.mp4"
    assert result.size == 5
    assert source.closed is True


def test_cli_put_dry_run_works_with_source_only_config(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"video")
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]
default_source = "main"

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "MISSING_TOKEN_IS_OK_FOR_DRY_RUN"
source = "projects"
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--config",
            str(config_path),
            "--non-interactive",
            "put",
            str(local),
            "/shows/demo/output.mp4",
            "--dry-run",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["source_name"] == "main"
    assert payload["size"] == 5
    assert payload["dry_run"] is True


def test_cli_get_dry_run_rejects_existing_destination(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    local = tmp_path / "output.mp4"
    local.write_bytes(b"existing")
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "MISSING_TOKEN_IS_OK_FOR_DRY_RUN"
source = "projects"
""",
        encoding="utf-8",
    )

    result = main(
        [
            "--config",
            str(config_path),
            "--non-interactive",
            "get",
            "/shows/demo/video.mp4",
            str(local),
            "--dry-run",
            "--json",
        ]
    )

    assert result == 1
    assert "already exists" in capsys.readouterr().err


class FakeCdnManager:
    name = "archive"

    def __init__(self, *, status: str = "submitted", task_id: str = "task-1") -> None:
        self.status = status
        self.task_id = task_id
        self.purged: list[list[str]] = []

    def purge_url(self, urls: list[str]) -> CdnTaskResult:
        self.purged.append(list(urls))
        return CdnTaskResult("purge_url", self.status, self.task_id, list(urls), "")

    def purge_path(self, paths: list[str], *, flush_type: str) -> CdnTaskResult:
        return CdnTaskResult("purge_path", self.status, self.task_id, list(paths), "")

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult:
        return CdnTaskResult("prefetch", self.status, self.task_id, list(urls), "")

    def build_url(self, object_key: str) -> str:
        return f"https://cdn.example.test/{object_key}"


def _cdn_skill_config(purge_on_upload: bool = True) -> SkillConfig:
    return SkillConfig(
        sources={
            "main": FileBrowserSourceConfig(
                name="main",
                base_url="https://files.example.test",
                token=SecretValue(direct="secret"),
                source="projects",
            )
        },
        targets={
            "archive": S3TargetConfig(
                name="archive",
                bucket="bucket",
                prefix="backup",
                cdn=CdnTargetConfig(
                    name="archive",
                    provider="tencent",
                    base_url="https://cdn.example.test",
                    purge_on_upload=purge_on_upload,
                    access_key_id=SecretValue(direct="ak"),
                    secret_access_key=SecretValue(direct="sk"),
                ),
            )
        },
        default_source="main",
        default_target="archive",
    )


def test_upload_purges_cdn_url_when_enabled(tmp_path: Path) -> None:
    source = FakeSource()
    target = FakeTarget()
    cdn = FakeCdnManager()
    service = TransferService(
        _cdn_skill_config(),
        source_factory=lambda _c: source,
        target_factory=lambda _c: target,
        cdn_factory=lambda _c: cdn,
    )
    result = service.upload("/shows/demo/video.mp4")

    assert result.cdn_task is not None
    assert result.cdn_task.status == "submitted"
    assert cdn.purged == [["https://cdn.example.test/backup/shows/demo/video.mp4"]]


def test_upload_succeeds_when_cdn_purge_fails(tmp_path: Path) -> None:
    source = FakeSource()
    target = FakeTarget()
    cdn = FakeCdnManager(status="failed", task_id="")
    service = TransferService(
        _cdn_skill_config(),
        source_factory=lambda _c: source,
        target_factory=lambda _c: target,
        cdn_factory=lambda _c: cdn,
    )
    result = service.upload("/shows/demo/video.mp4")

    assert result.object_key == "backup/shows/demo/video.mp4"
    assert result.cdn_task is not None
    assert result.cdn_task.status == "failed"


def test_upload_skips_cdn_when_purge_on_upload_disabled() -> None:
    source = FakeSource()
    target = FakeTarget()
    cdn = FakeCdnManager()
    service = TransferService(
        _cdn_skill_config(purge_on_upload=False),
        source_factory=lambda _c: source,
        target_factory=lambda _c: target,
        cdn_factory=lambda _c: cdn,
    )
    result = service.upload("/shows/demo/video.mp4")

    assert result.cdn_task is None
    assert cdn.purged == []


def test_cli_cdn_purge_url_dry_run(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]
default_source = "main"
default_target = "archive"

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "MISSING_TOKEN_OK"
source = "projects"

[filebrowser.targets.archive]
adapter = "s3"
bucket = "bucket"
prefix = "backup"
access_key_id_env = "MISSING_AK_OK"
secret_access_key_env = "MISSING_SK_OK"

[filebrowser.targets.archive.cdn]
provider = "tencent"
base_url = "https://cdn.example.test"
""",
        encoding="utf-8",
    )
    result = main(
        [
            "--config",
            str(config_path),
            "--non-interactive",
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
    assert payload["operation"] == "purge_url"
    assert payload["targets"] == ["https://cdn.example.test/a/b.mp4"]
    assert payload["dry_run"] is True
