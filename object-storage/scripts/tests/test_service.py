from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from object_storage.models import (
    CdnConfig,
    CdnTaskResult,
    ObjectStorageConfig,
    S3TargetConfig,
    UploadResult,
)
from object_storage.service import ObjectStorageService


class FakeTarget:
    def __init__(self, *, skipped: bool = False) -> None:
        self.skipped = skipped
        self.writable_calls: list[tuple[str, bool]] = []

    def resolve_key(self, relative_key: str) -> str:
        return f"prefix/{relative_key}"

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> None:
        self.writable_calls.append((object_key, overwrite))

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str = "",
        if_changed: bool = False,
    ) -> UploadResult:
        del content_type, if_changed
        return UploadResult(
            target_name="archive",
            bucket="bucket",
            object_key=object_key,
            size=local_path.stat().st_size,
            etag="etag",
            version_id="",
            public_url="",
            skipped_unchanged=self.skipped,
            content_sha256="abc",
        )


class FakeCdn:
    def __init__(self) -> None:
        self.purged: list[str] = []

    def build_url(self, object_key: str) -> str:
        return f"https://cdn.example.test/{object_key}"

    def purge_url(self, urls: list[str]) -> CdnTaskResult:
        self.purged.extend(urls)
        return CdnTaskResult("purge_url", "submitted", "task-1", urls)

    def purge_path(
        self,
        paths: list[str],
        *,
        flush_type: Literal["flush", "delete"],
    ) -> CdnTaskResult:
        del flush_type
        return CdnTaskResult("purge_path", "submitted", "task-2", paths)

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult:
        del area
        return CdnTaskResult("prefetch", "submitted", "task-3", urls)


def _config(*, purge_on_upload: bool = False) -> ObjectStorageConfig:
    cdn = (
        CdnConfig(
            name="archive",
            provider="tencent",
            base_url="https://cdn.example.test",
            purge_on_upload=True,
        )
        if purge_on_upload
        else None
    )
    target = S3TargetConfig(name="archive", bucket="bucket", prefix="prefix", cdn=cdn)
    return ObjectStorageConfig(targets={"archive": target}, default_target="archive")


def test_if_changed_requires_overwrite(tmp_path: Path) -> None:
    local = tmp_path / "a.txt"
    local.write_text("a", encoding="utf-8")
    service = ObjectStorageService(_config(), target_factory=lambda _config: FakeTarget())
    with pytest.raises(ValueError, match="requires --overwrite"):
        service.plan(local, if_changed=True)


def test_unchanged_result_explicitly_lists_local_file(tmp_path: Path) -> None:
    local = tmp_path / "a.txt"
    local.write_text("a", encoding="utf-8")
    target = FakeTarget(skipped=True)
    service = ObjectStorageService(_config(), target_factory=lambda _config: target)

    result = service.upload(local, object_key="dir/a.txt", overwrite=True, if_changed=True)

    assert result.skipped_unchanged is True
    assert len(result.unchanged_files) == 1
    assert result.unchanged_files[0].source_path == str(local.resolve())
    assert result.unchanged_files[0].object_key == "prefix/dir/a.txt"


def test_changed_upload_purges_cdn_but_unchanged_upload_does_not(tmp_path: Path) -> None:
    local = tmp_path / "a.txt"
    local.write_text("a", encoding="utf-8")
    cdn = FakeCdn()
    changed = ObjectStorageService(
        _config(purge_on_upload=True),
        target_factory=lambda _config: FakeTarget(),
        cdn_factory=lambda _config: cdn,
    )
    changed_result = changed.upload(local, object_key="a.txt")

    unchanged = ObjectStorageService(
        _config(purge_on_upload=True),
        target_factory=lambda _config: FakeTarget(skipped=True),
        cdn_factory=lambda _config: cdn,
    )
    unchanged_result = unchanged.upload(local, object_key="a.txt", overwrite=True, if_changed=True)

    assert changed_result.cdn_task is not None
    assert cdn.purged == ["https://cdn.example.test/prefix/a.txt"]
    assert unchanged_result.cdn_task is None
