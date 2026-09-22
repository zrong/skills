"""对象存储与 CDN 操作编排。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Protocol

from .cdn import CdnCacheManager, build_cdn_cache_manager, build_cdn_url
from .models import (
    CdnTaskResult,
    DownloadFailure,
    DownloadPlan,
    DownloadResult,
    ObjectHeadResult,
    ObjectStorageConfig,
    RemoteObject,
    S3TargetConfig,
    TreeDownloadPlan,
    TreeDownloadResult,
    TreeUploadPlan,
    TreeUploadResult,
    UnchangedFile,
    UploadFailure,
    UploadPlan,
    UploadResult,
)
from .target import S3Target, TargetError, normalize_object_key, resolve_target_key


class ObjectStorageTarget(Protocol):
    def resolve_key(self, relative_key: str) -> str: ...

    def ensure_writable(
        self, object_key: str, *, overwrite: bool
    ) -> Mapping[str, object] | None: ...

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
    ) -> UploadResult: ...

    def list_prefix(self, object_prefix: str) -> list[RemoteObject]: ...

    def download(self, object_key: str, output_path: Path) -> DownloadResult: ...

    def head(self, object_key: str) -> ObjectHeadResult: ...


type TargetFactory = Callable[[S3TargetConfig], ObjectStorageTarget]
type CdnFactory = Callable[[S3TargetConfig], CdnCacheManager | None]


def normalize_cache_control(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("--cache-control must not be empty")
    if "\r" in normalized or "\n" in normalized:
        raise ValueError("--cache-control must not contain a line break")
    return normalized


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
        cache_control: str | None = None,
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
            cache_control=normalize_cache_control(cache_control),
        )

    def plan_tree(
        self,
        source_directory: str | Path,
        *,
        target_name: str | None = None,
        key_prefix: str = "",
        overwrite: bool = False,
        if_changed: bool = False,
        cache_control: str | None = None,
        workers: int = 4,
    ) -> TreeUploadPlan:
        if if_changed and not overwrite:
            raise ValueError("--if-changed requires --overwrite")
        if workers < 1:
            raise ValueError("--workers must be at least 1")
        source = Path(source_directory).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"Local path is not a directory: {source}")
        normalized_prefix = normalize_object_key(key_prefix) if key_prefix else ""
        files = sorted(
            (path for path in source.rglob("*") if path.is_file() and not path.is_symlink()),
            key=lambda path: path.relative_to(source).as_posix(),
        )
        if not files:
            raise ValueError(f"Local directory contains no regular files: {source}")
        plans = [
            self.plan(
                path,
                target_name=target_name,
                object_key="/".join(
                    part
                    for part in (normalized_prefix, path.relative_to(source).as_posix())
                    if part
                ),
                overwrite=overwrite,
                if_changed=if_changed,
                cache_control=cache_control,
            )
            for path in files
        ]
        return TreeUploadPlan(
            source_directory=str(source),
            target_name=plans[0].target_name,
            key_prefix=normalized_prefix,
            files=plans,
            workers=workers,
        )

    def resolve_key(self, relative_key: str, *, target_name: str | None = None) -> str:
        target_config = self.config.target(target_name)
        return resolve_target_key(target_config, relative_key)

    def head(self, object_key: str, *, target_name: str | None = None) -> ObjectHeadResult:
        target_config = self.config.target(target_name)
        target = self._target_factory(target_config)
        return target.head(resolve_target_key(target_config, object_key))

    def upload(
        self,
        local_path: str | Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
        content_type: str = "",
        cache_control: str | None = None,
    ) -> UploadResult:
        plan = self.plan(
            local_path,
            target_name=target_name,
            object_key=object_key,
            overwrite=overwrite,
            if_changed=if_changed,
            cache_control=cache_control,
        )
        target_config = self.config.target(plan.target_name)
        target = self._target_factory(target_config)
        result = self._upload_plan(target, plan, content_type=content_type)
        if result.skipped_unchanged:
            return result
        return replace(result, cdn_task=self._purge_single_upload(target_config, plan.object_key))

    @staticmethod
    def _upload_plan(
        target: ObjectStorageTarget,
        plan: UploadPlan,
        *,
        content_type: str = "",
        ensure_writable: bool = True,
        existing: Mapping[str, object] | None = None,
        existing_checked: bool = False,
    ) -> UploadResult:
        if ensure_writable:
            existing = target.ensure_writable(plan.object_key, overwrite=plan.overwrite)
            existing_checked = True
        result = target.upload(
            Path(plan.local_path),
            plan.object_key,
            content_type=content_type,
            if_changed=plan.if_changed,
            cache_control=plan.cache_control,
            existing=existing,
            existing_checked=existing_checked,
        )
        if result.skipped_unchanged:
            return replace(
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

    def _purge_single_upload(
        self, target_config: S3TargetConfig, object_key: str
    ) -> CdnTaskResult | None:
        if target_config.cdn is None or not target_config.cdn.purge_on_upload:
            return None
        url = build_cdn_url(target_config.cdn.base_url, object_key)
        try:
            manager = self._cdn_factory(target_config)
            return manager.purge_url([url]) if manager is not None else None
        except Exception as exc:
            return CdnTaskResult(
                operation="purge_url",
                status="failed",
                task_id="",
                targets=[url],
                error=str(exc),
            )

    def upload_tree(
        self,
        source_directory: str | Path,
        *,
        target_name: str | None = None,
        key_prefix: str = "",
        overwrite: bool = False,
        if_changed: bool = False,
        cache_control: str | None = None,
        workers: int = 4,
    ) -> TreeUploadResult:
        tree_plan = self.plan_tree(
            source_directory,
            target_name=target_name,
            key_prefix=key_prefix,
            overwrite=overwrite,
            if_changed=if_changed,
            cache_control=cache_control,
            workers=workers,
        )
        target_config = self.config.target(tree_plan.target_name)
        target = self._target_factory(target_config)

        # Refuse the whole tree before the first write if any destination already exists.
        # Existing metadata is also reused to preserve Cache-Control during overwrites.
        existing_by_key: dict[str, Mapping[str, object] | None] = {}
        for plan in tree_plan.files:
            existing_by_key[plan.object_key] = target.ensure_writable(
                plan.object_key, overwrite=overwrite
            )

        def execute(plan: UploadPlan) -> UploadResult | UploadFailure:
            try:
                return self._upload_plan(
                    target,
                    plan,
                    ensure_writable=False,
                    existing=existing_by_key[plan.object_key],
                    existing_checked=True,
                )
            except (TargetError, ValueError) as exc:
                return UploadFailure(plan.local_path, plan.object_key, str(exc))
            except Exception as exc:  # Keep unexpected SDK details and credentials out of JSON.
                return UploadFailure(plan.local_path, plan.object_key, type(exc).__name__)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            outcomes = list(executor.map(execute, tree_plan.files))

        results = [item for item in outcomes if isinstance(item, UploadResult)]
        failures = [item for item in outcomes if isinstance(item, UploadFailure)]
        changed = [result for result in results if not result.skipped_unchanged]
        cdn_tasks = self._purge_tree_upload(target_config, tree_plan, changed)
        return TreeUploadResult(
            source_directory=tree_plan.source_directory,
            target_name=tree_plan.target_name,
            total_files=len(tree_plan.files),
            uploaded_files=len(changed),
            skipped_files=len(results) - len(changed),
            failed_files=len(failures),
            uploaded_bytes=sum(result.size for result in changed),
            results=results,
            failures=failures,
            cdn_tasks=cdn_tasks,
        )

    def plan_download(
        self,
        object_key: str,
        *,
        output_path: str | Path,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> DownloadPlan:
        target_config = self.config.target(target_name)
        output = Path(output_path).expanduser().resolve()
        if output.exists() and output.is_dir():
            raise ValueError(f"Download output is a directory: {output}")
        return DownloadPlan(
            target_name=target_config.name,
            bucket=target_config.bucket,
            object_key=resolve_target_key(target_config, object_key),
            output_path=str(output),
            overwrite=overwrite,
        )

    @staticmethod
    def _ensure_download_output_writable(plan: DownloadPlan) -> None:
        output = Path(plan.output_path)
        if output.exists() and not plan.overwrite:
            raise ValueError(
                f"Download output already exists: {output}; use --overwrite to replace it"
            )

    def download(
        self,
        object_key: str,
        *,
        output_path: str | Path,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> DownloadResult:
        plan = self.plan_download(
            object_key,
            output_path=output_path,
            target_name=target_name,
            overwrite=overwrite,
        )
        self._ensure_download_output_writable(plan)
        target = self._target_factory(self.config.target(plan.target_name))
        return target.download(plan.object_key, Path(plan.output_path))

    def plan_download_tree(
        self,
        source_prefix: str,
        *,
        output_directory: str | Path,
        target_name: str | None = None,
        overwrite: bool = False,
        workers: int = 4,
    ) -> TreeDownloadPlan:
        target_config = self.config.target(target_name)
        target = self._target_factory(target_config)
        return self._build_download_tree_plan(
            target,
            target_config,
            source_prefix,
            output_directory=output_directory,
            overwrite=overwrite,
            workers=workers,
        )

    def _build_download_tree_plan(
        self,
        target: ObjectStorageTarget,
        target_config: S3TargetConfig,
        source_prefix: str,
        *,
        output_directory: str | Path,
        overwrite: bool,
        workers: int,
    ) -> TreeDownloadPlan:
        if workers < 1:
            raise ValueError("--workers must be at least 1")
        normalized_prefix = normalize_object_key(source_prefix)
        object_prefix = resolve_target_key(target_config, normalized_prefix)
        output_root = Path(output_directory).expanduser().resolve()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"Download output is not a directory: {output_root}")
        source_prefix_with_separator = f"{object_prefix}/"
        plans: list[DownloadPlan] = []
        output_paths: set[Path] = set()
        for remote in target.list_prefix(object_prefix):
            if not remote.object_key.startswith(source_prefix_with_separator):
                raise TargetError(
                    "S3 list_objects_v2 returned an object outside the requested prefix"
                )
            relative_key = normalize_object_key(
                remote.object_key.removeprefix(source_prefix_with_separator)
            )
            output = (output_root / Path(*PurePosixPath(relative_key).parts)).resolve()
            if not output.is_relative_to(output_root):
                raise TargetError("S3 object key resolves outside the requested output directory")
            if output in output_paths:
                raise TargetError(f"Multiple S3 objects map to one output path: {output}")
            output_paths.add(output)
            plans.append(
                DownloadPlan(
                    target_name=target_config.name,
                    bucket=target_config.bucket,
                    object_key=remote.object_key,
                    output_path=str(output),
                    overwrite=overwrite,
                )
            )
        if not plans:
            raise ValueError(f"No regular objects found under prefix: {object_prefix}")
        for plan in plans:
            self._ensure_download_output_writable(plan)
        return TreeDownloadPlan(
            source_prefix=object_prefix,
            target_name=target_config.name,
            output_directory=str(output_root),
            files=plans,
            workers=workers,
        )

    def download_tree(
        self,
        source_prefix: str,
        *,
        output_directory: str | Path,
        target_name: str | None = None,
        overwrite: bool = False,
        workers: int = 4,
    ) -> TreeDownloadResult:
        target_config = self.config.target(target_name)
        target = self._target_factory(target_config)
        tree_plan = self._build_download_tree_plan(
            target,
            target_config,
            source_prefix,
            output_directory=output_directory,
            overwrite=overwrite,
            workers=workers,
        )

        def execute(plan: DownloadPlan) -> DownloadResult | DownloadFailure:
            try:
                self._ensure_download_output_writable(plan)
                return target.download(plan.object_key, Path(plan.output_path))
            except (TargetError, ValueError) as exc:
                return DownloadFailure(plan.object_key, plan.output_path, str(exc))
            except Exception as exc:
                return DownloadFailure(plan.object_key, plan.output_path, type(exc).__name__)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            outcomes = list(executor.map(execute, tree_plan.files))
        results = [item for item in outcomes if isinstance(item, DownloadResult)]
        failures = [item for item in outcomes if isinstance(item, DownloadFailure)]
        return TreeDownloadResult(
            source_prefix=tree_plan.source_prefix,
            target_name=tree_plan.target_name,
            output_directory=tree_plan.output_directory,
            total_files=len(tree_plan.files),
            downloaded_files=len(results),
            failed_files=len(failures),
            downloaded_bytes=sum(result.size for result in results),
            results=results,
            failures=failures,
        )

    def _purge_tree_upload(
        self,
        target_config: S3TargetConfig,
        tree_plan: TreeUploadPlan,
        changed: list[UploadResult],
    ) -> list[CdnTaskResult]:
        if not changed or target_config.cdn is None or not target_config.cdn.purge_on_upload:
            return []
        manager = self._cdn_factory(target_config)
        if manager is None:
            return []

        scope = "/".join(part for part in (target_config.prefix, tree_plan.key_prefix) if part)
        directory_keys: list[str]
        file_keys: list[str]
        if scope:
            directory_keys = [scope]
            file_keys = []
        else:
            directory_keys = sorted(
                {
                    result.object_key.split("/", 1)[0]
                    for result in changed
                    if "/" in result.object_key
                }
            )
            file_keys = sorted(
                result.object_key for result in changed if "/" not in result.object_key
            )

        tasks: list[CdnTaskResult] = []
        try:
            if directory_keys:
                tasks.append(
                    manager.purge_path(
                        [manager.build_url(key) for key in directory_keys], flush_type="flush"
                    )
                )
            if file_keys:
                tasks.append(manager.purge_url([manager.build_url(key) for key in file_keys]))
        except Exception as exc:
            targets = [*directory_keys, *file_keys]
            tasks.append(CdnTaskResult("purge_tree", "failed", "", targets, str(exc)))
        return tasks

    def cdn_manager(self, target_name: str | None = None) -> CdnCacheManager:
        target = self.config.target(target_name)
        manager = self._cdn_factory(target)
        if manager is None:
            raise ValueError(f"Target {target.name} has no CDN configuration")
        return manager
