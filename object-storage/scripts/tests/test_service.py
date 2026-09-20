from __future__ import annotations

from collections.abc import Mapping
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
from object_storage.target import TargetError


class FakeTarget:
    def __init__(self, *, skipped: bool = False, existing: set[str] | None = None) -> None:
        self.skipped = skipped
        self.existing = existing or set()
        self.writable_calls: list[tuple[str, bool]] = []
        self.upload_calls: list[tuple[Path, str]] = []

    def resolve_key(self, relative_key: str) -> str:
        return f"prefix/{relative_key}"

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> Mapping[str, object] | None:
        self.writable_calls.append((object_key, overwrite))
        if object_key in self.existing and not overwrite:
            raise TargetError(f"already exists: {object_key}")
        return {} if object_key in self.existing else None

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str = "",
        if_changed: bool = False,
        cache_control: str | None = None,
        existing: Mapping[str, object] | None = None,
        existing_checked: bool = False,
    ) -> UploadResult:
        del content_type, if_changed, cache_control, existing, existing_checked
        self.upload_calls.append((local_path, object_key))
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
        self.purged_paths: list[str] = []

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
        self.purged_paths.extend(paths)
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


def test_plan_tree_recursively_preserves_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("index", encoding="utf-8")
    (source / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
    service = ObjectStorageService(_config(), target_factory=lambda _config: FakeTarget())

    plan = service.plan_tree(source, key_prefix="releases/v1")

    assert [item.object_key for item in plan.files] == [
        "prefix/releases/v1/assets/logo.txt",
        "prefix/releases/v1/index.html",
    ]
    assert all("dist/" not in item.object_key for item in plan.files)


def test_upload_tree_reuses_target_and_purges_one_directory(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("index", encoding="utf-8")
    (source / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
    target = FakeTarget()
    target_factory_calls = 0
    cdn = FakeCdn()

    def target_factory(_config: S3TargetConfig) -> FakeTarget:
        nonlocal target_factory_calls
        target_factory_calls += 1
        return target

    service = ObjectStorageService(
        _config(purge_on_upload=True),
        target_factory=target_factory,
        cdn_factory=lambda _config: cdn,
    )

    result = service.upload_tree(source, key_prefix="releases/v1", workers=2)

    assert target_factory_calls == 1
    assert result.total_files == 2
    assert result.uploaded_files == 2
    assert result.failed_files == 0
    assert len(target.upload_calls) == 2
    assert cdn.purged == []
    assert cdn.purged_paths == ["https://cdn.example.test/prefix/releases/v1"]


def test_upload_tree_preflights_all_destinations_before_writing(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "b.txt").write_text("b", encoding="utf-8")
    target = FakeTarget(existing={"prefix/b.txt"})
    service = ObjectStorageService(_config(), target_factory=lambda _config: target)

    with pytest.raises(TargetError, match="already exists"):
        service.upload_tree(source)

    assert target.upload_calls == []


def test_upload_tree_does_not_purge_when_every_file_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    cdn = FakeCdn()
    service = ObjectStorageService(
        _config(purge_on_upload=True),
        target_factory=lambda _config: FakeTarget(skipped=True),
        cdn_factory=lambda _config: cdn,
    )

    result = service.upload_tree(source, overwrite=True, if_changed=True)

    assert result.uploaded_files == 0
    assert result.skipped_files == 1
    assert result.cdn_tasks == []
    assert cdn.purged == []
    assert cdn.purged_paths == []


def test_upload_tree_without_destination_prefix_avoids_full_site_purge(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("index", encoding="utf-8")
    (source / "assets" / "logo.txt").write_text("logo", encoding="utf-8")
    cdn = FakeCdn()
    target_config = S3TargetConfig(
        name="root",
        bucket="bucket",
        cdn=CdnConfig(
            name="root",
            provider="tencent",
            base_url="https://cdn.example.test",
            purge_on_upload=True,
        ),
    )
    config = ObjectStorageConfig(targets={"root": target_config}, default_target="root")
    service = ObjectStorageService(
        config,
        target_factory=lambda _config: FakeTarget(),
        cdn_factory=lambda _config: cdn,
    )

    result = service.upload_tree(source)

    assert result.uploaded_files == 2
    assert cdn.purged_paths == ["https://cdn.example.test/assets"]
    assert cdn.purged == ["https://cdn.example.test/index.html"]
