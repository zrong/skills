"""Transfer orchestration independent of concrete target adapters."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol, cast

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
    UnchangedFile,
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


class FileManagementSource(Protocol):
    def info(
        self,
        path: str,
        *,
        content: bool = False,
        checksum: str | None = None,
    ) -> dict[str, object]: ...

    def exists(self, path: str) -> bool: ...

    def list_files(self, path: str) -> list[dict[str, object]]: ...

    def ensure_dir(self, path: str) -> str: ...

    def update_file(self, path: str, content: bytes, *, overwrite: bool = False) -> None: ...

    def delete(self, path: str) -> None: ...

    def move(
        self,
        from_path: str,
        to_path: str,
        *,
        action: Literal["rename", "copy"] = "rename",
        overwrite: bool = False,
    ) -> None: ...

    def search(self, query: str, *, scope: str | None = None) -> list[dict[str, object]]: ...

    def preview(
        self,
        path: str,
        size: str | None = None,
        output: str | None = None,
    ) -> Path: ...

    def list_sources(self) -> list[dict[str, object]]: ...

    def upload_file_to_dir(
        self,
        local_path: str | Path,
        remote_dir: str,
        *,
        remote_name: str | None = None,
        overwrite: bool = False,
    ) -> RemoteFile: ...

    def download_files(
        self,
        files: list[str],
        *,
        algo: str = "zip",
        output: str | Path | None = None,
    ) -> Path: ...


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

    @staticmethod
    def _management_source(source: FileSource) -> FileManagementSource:
        """Narrow a routed source to the optional file-management surface."""
        return cast(FileManagementSource, source)

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
        if_changed: bool = False,
    ) -> UploadResult:
        if if_changed and not overwrite:
            raise FileBrowserError("--if-changed requires --overwrite")
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
                if_changed=if_changed,
            )
        if result.skipped_unchanged:
            result = replace(
                result,
                unchanged_files=[
                    UnchangedFile(
                        source_path=plan.remote_path,
                        object_key=plan.object_key,
                        size=result.size,
                        content_sha256=result.content_sha256,
                    )
                ],
            )
        return self._with_cdn_task(target_config, plan.object_key, result)

    def _with_cdn_task(
        self,
        target_config: TargetConfig,
        object_key: str,
        result: UploadResult,
    ) -> UploadResult:
        if result.skipped_unchanged:
            return result
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

    # ------------------------------------------------------------------
    # File management operations. These are pure pass-throughs to the
    # underlying FileBrowser client; ``TransferService`` is the multi-
    # source routing layer so callers never construct clients by hand.
    # ------------------------------------------------------------------

    def info(
        self,
        path: str,
        *,
        source_name: str | None = None,
        content: bool = False,
        checksum: str | None = None,
    ) -> dict[str, object]:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).info(path, content=content, checksum=checksum)

    def exists(self, path: str, *, source_name: str | None = None) -> bool:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).exists(path)

    def list_files(self, path: str, *, source_name: str | None = None) -> list[dict[str, object]]:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).list_files(path)

    def ensure_dir(self, path: str, *, source_name: str | None = None) -> str:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).ensure_dir(path)

    def update_file(
        self,
        path: str,
        content: bytes,
        *,
        source_name: str | None = None,
        overwrite: bool = False,
    ) -> None:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            self._management_source(source).update_file(path, content, overwrite=overwrite)

    def delete(self, path: str, *, source_name: str | None = None) -> None:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            self._management_source(source).delete(path)

    def move(
        self,
        from_path: str,
        to_path: str,
        *,
        action: Literal["rename", "copy"] = "rename",
        overwrite: bool = False,
        source_name: str | None = None,
    ) -> None:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            self._management_source(source).move(
                from_path,
                to_path,
                action=action,
                overwrite=overwrite,
            )

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        source_name: str | None = None,
    ) -> list[dict[str, object]]:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).search(query, scope=scope)

    def preview(
        self,
        path: str,
        *,
        size: str | None = None,
        output: str | Path | None = None,
        source_name: str | None = None,
    ) -> Path:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).preview(
                path,
                size=size,
                output=str(output) if output else None,
            )

    def list_sources(self, *, source_name: str | None = None) -> list[dict[str, object]]:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).list_sources()

    def upload_file_to_dir(
        self,
        local_path: str | Path,
        remote_dir: str,
        *,
        remote_name: str | None = None,
        overwrite: bool = False,
        source_name: str | None = None,
    ) -> RemoteFile:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).upload_file_to_dir(
                local_path,
                remote_dir,
                remote_name=remote_name,
                overwrite=overwrite,
            )

    def download_files(
        self,
        files: list[str],
        *,
        algo: str = "zip",
        output: str | Path | None = None,
        source_name: str | None = None,
    ) -> Path:
        source_config = self.config.source(source_name)
        with self._source_factory(source_config) as source:
            return self._management_source(source).download_files(files, algo=algo, output=output)
