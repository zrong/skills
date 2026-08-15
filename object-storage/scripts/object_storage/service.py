"""对象存储与 CDN 操作编排。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from .cdn import CdnCacheManager, build_cdn_cache_manager, build_cdn_url
from .models import (
    CdnTaskResult,
    ObjectStorageConfig,
    S3TargetConfig,
    UnchangedFile,
    UploadPlan,
    UploadResult,
)
from .target import S3Target, resolve_target_key


class UploadTarget(Protocol):
    def resolve_key(self, relative_key: str) -> str: ...

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> None: ...

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str = "",
        if_changed: bool = False,
    ) -> UploadResult: ...


type TargetFactory = Callable[[S3TargetConfig], UploadTarget]
type CdnFactory = Callable[[S3TargetConfig], CdnCacheManager | None]


class ObjectStorageService:
    def __init__(
        self,
        config: ObjectStorageConfig,
        *,
        target_factory: TargetFactory = S3Target,
        cdn_factory: CdnFactory = build_cdn_cache_manager,
    ) -> None:
        self.config = config
        self._target_factory = target_factory
        self._cdn_factory = cdn_factory

    def plan(
        self,
        local_path: str | Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
    ) -> UploadPlan:
        if if_changed and not overwrite:
            raise ValueError("--if-changed requires --overwrite")
        local = Path(local_path).expanduser().resolve()
        if not local.is_file():
            raise ValueError(f"Local path is not a file: {local}")
        target_config = self.config.target(target_name)
        relative_key = object_key or local.name
        return UploadPlan(
            local_path=str(local),
            target_name=target_config.name,
            object_key=resolve_target_key(target_config, relative_key),
            overwrite=overwrite,
            if_changed=if_changed,
        )

    def resolve_key(self, relative_key: str, *, target_name: str | None = None) -> str:
        target_config = self.config.target(target_name)
        return resolve_target_key(target_config, relative_key)

    def upload(
        self,
        local_path: str | Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
        content_type: str = "",
    ) -> UploadResult:
        plan = self.plan(
            local_path,
            target_name=target_name,
            object_key=object_key,
            overwrite=overwrite,
            if_changed=if_changed,
        )
        target_config = self.config.target(plan.target_name)
        target = self._target_factory(target_config)
        target.ensure_writable(plan.object_key, overwrite=overwrite)
        result = target.upload(
            Path(plan.local_path),
            plan.object_key,
            content_type=content_type,
            if_changed=if_changed,
        )
        if result.skipped_unchanged:
            result = replace(
                result,
                unchanged_files=[
                    UnchangedFile(
                        source_path=plan.local_path,
                        object_key=plan.object_key,
                        size=result.size,
                        content_sha256=result.content_sha256,
                    )
                ],
            )
            return result
        if target_config.cdn is None or not target_config.cdn.purge_on_upload:
            return result
        url = build_cdn_url(target_config.cdn.base_url, plan.object_key)
        try:
            manager = self._cdn_factory(target_config)
            task = manager.purge_url([url]) if manager is not None else None
        except Exception as exc:
            task = CdnTaskResult(
                operation="purge_url",
                status="failed",
                task_id="",
                targets=[url],
                error=str(exc),
            )
        return replace(result, cdn_task=task)

    def cdn_manager(self, target_name: str | None = None) -> CdnCacheManager:
        target = self.config.target(target_name)
        manager = self._cdn_factory(target)
        if manager is None:
            raise ValueError(f"Target {target.name} has no CDN configuration")
        return manager
