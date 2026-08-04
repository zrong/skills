"""Transfer orchestration independent of concrete target adapters."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .filebrowser import FileBrowserClient, normalize_remote_path
from .models import (
    FileBrowserSourceConfig,
    RemoteFile,
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


type SourceFactory = Callable[[FileBrowserSourceConfig], FileSource]
type TargetFactory = Callable[[TargetConfig], UploadTarget]


class TransferService:
    def __init__(
        self,
        config: SkillConfig,
        *,
        source_factory: SourceFactory = FileBrowserClient,
        target_factory: TargetFactory = build_target,
    ) -> None:
        self.config = config
        self._source_factory = source_factory
        self._target_factory = target_factory

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
                written = source.download(remote, local_path)
            if remote.size is not None and written != remote.size:
                raise RuntimeError(
                    f"Staged file size mismatch: expected {remote.size}, downloaded {written}"
                )
            return target.upload(
                local_path,
                plan.object_key,
                content_type=remote.content_type,
            )
