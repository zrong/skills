"""Transfer orchestration independent of concrete target adapters."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .cdn import CdnCacheManager, build_cdn_cache_manager, build_cdn_url
from .filebrowser import FileBrowserClient, FileBrowserError, normalize_remote_path
from .models import (
    CdnTaskResult,
    FileBrowserSourceConfig,
    GetPlan,
    GetResult,
    PutPlan,
    PutResult,
    RemoteFile,
    S3TargetConfig,
    SkillConfig,
    TargetConfig,
    TransferPlan,
    UploadResult,
)
from .targets import UploadTarget, build_target, normalize_object_key, resolve_target_key


class FileSource(Protocol):
    def __enter__(self) -> FileSource: ...

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None: ...

    def metadata(self, remote_path: str) -> RemoteFile: ...

    def download(self, remote: RemoteFile, destination: Path) -> int: ...

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        overwrite: bool = False,
    ) -> RemoteFile: ...


type SourceFactory = Callable[[FileBrowserSourceConfig], FileSource]
type TargetFactory = Callable[[TargetConfig], UploadTarget]
type CdnFactory = Callable[[S3TargetConfig], CdnCacheManager | None]


class TransferService:
    def __init__(
        self,
        config: SkillConfig,
        *,
        source_factory: SourceFactory = FileBrowserClient,
        target_factory: TargetFactory = build_target,
        cdn_factory: CdnFactory = build_cdn_cache_manager,
    ) -> None:
        self.config = config
        self._source_factory = source_factory
        self._target_factory = target_factory
        self._cdn_factory = cdn_factory

    def plan(
        self,
        remote_path: str,
        *,
        source_name: str | None = None,
        target_name: str | None = None,
        object_key: str | None = None,
    ) -> TransferPlan:
        source = self.config.source(source_name)
        target_config = self.config.target(target_name)
        normalized_remote = normalize_remote_path(remote_path)
        relative_key = normalize_object_key(object_key or normalized_remote.lstrip("/"))
        return TransferPlan(
            source_name=source.name,
            remote_path=normalized_remote,
            target_name=target_config.name,
            object_key=resolve_target_key(target_config, relative_key),
        )

    def put_plan(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        source_name: str | None = None,
    ) -> PutPlan:
        local = Path(local_path).expanduser().resolve()
        if not local.is_file():
            raise FileBrowserError(f"Local path is not a file: {local}")
        source = self.config.source(source_name)
        size = local.stat().st_size
        if source.max_transfer_bytes > 0 and size > source.max_transfer_bytes:
            raise FileBrowserError(
                f"Local file exceeds max_transfer_bytes ({size} > {source.max_transfer_bytes})"
            )
        return PutPlan(
            source_name=source.name,
            local_path=str(local),
            remote_path=normalize_remote_path(remote_path),
            size=size,
        )

    def get_plan(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        source_name: str | None = None,
    ) -> GetPlan:
        local = Path(local_path).expanduser().resolve()
        if local.exists():
            raise FileBrowserError(f"Local destination already exists: {local}")
        source = self.config.source(source_name)
        return GetPlan(
            source_name=source.name,
            remote_path=normalize_remote_path(remote_path),
            local_path=str(local),
        )

    def get(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        source_name: str | None = None,
    ) -> GetResult:
        plan = self.get_plan(remote_path, local_path, source_name=source_name)
        source_config = self.config.source(plan.source_name)
        with self._source_factory(source_config) as source:
            remote = source.metadata(plan.remote_path)
            written = source.download(remote, Path(plan.local_path))
        return GetResult(
            source_name=plan.source_name,
            remote_path=plan.remote_path,
            local_path=plan.local_path,
            size=written,
        )

    def put(
        self,
        local_path: str | Path,
        remote_path: str,
        *,
        source_name: str | None = None,
        overwrite: bool = False,
    ) -> PutResult:
        plan = self.put_plan(local_path, remote_path, source_name=source_name)
        source_config = self.config.source(plan.source_name)
        with self._source_factory(source_config) as source:
            remote = source.upload_file(
                Path(plan.local_path),
                plan.remote_path,
                overwrite=overwrite,
            )
        if remote.size != plan.size:
            raise RuntimeError(
                f"Uploaded file size mismatch: expected {plan.size}, uploaded {remote.size}"
            )
        return PutResult(
            source_name=plan.source_name,
            local_path=plan.local_path,
            remote_path=plan.remote_path,
            size=plan.size,
        )

    def upload(
        self,
        remote_path: str,
        *,
        source_name: str | None = None,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
    ) -> UploadResult:
        source_config = self.config.source(source_name)
        target_config = self.config.target(target_name)
        target = self._target_factory(target_config)
        plan = self.plan(
            remote_path,
            source_name=source_config.name,
            target_name=target_config.name,
            object_key=object_key,
        )
        target.ensure_writable(plan.object_key, overwrite=overwrite)

        staging_root = self.config.staging_dir
        if staging_root is not None:
            staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="filebrowser-transfer-",
            dir=staging_root,
        ) as temporary_dir:
            local_path = Path(temporary_dir) / "payload"
            with self._source_factory(source_config) as source:
                remote = source.metadata(plan.remote_path)
                source.download(remote, local_path)
            result = target.upload(
                local_path,
                plan.object_key,
                content_type=remote.content_type,
            )
        return self._with_cdn_task(target_config, plan.object_key, result)

    def _with_cdn_task(
        self,
        target_config: TargetConfig,
        object_key: str,
        result: UploadResult,
    ) -> UploadResult:
        cdn_config = target_config.cdn
        if cdn_config is None or not cdn_config.purge_on_upload:
            return result
        url = build_cdn_url(cdn_config.base_url, object_key)
        try:
            manager = self._cdn_factory(target_config)
            if manager is None:
                return result
            task = manager.purge_url([url])
        except Exception as exc:
            task = CdnTaskResult(
                operation="purge_url",
                status="failed",
                task_id="",
                targets=[url],
                error=str(exc),
            )
        return replace(result, cdn_task=task)
