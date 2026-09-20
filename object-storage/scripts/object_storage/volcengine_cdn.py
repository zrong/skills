"""火山引擎 CDN 缓存刷新与任务状态查询。"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from .cdn import build_cdn_url
from .models import CdnConfig, CdnTaskResult, ConfigurationError


class VolcengineCdnClientProtocol(Protocol):
    def submit_refresh_task(self, body: Any) -> Any: ...

    def submit_preload_task(self, body: Any) -> Any: ...

    def describe_content_tasks(self, body: Any) -> Any: ...


class VolcengineCdnCacheManager:
    def __init__(
        self,
        config: CdnConfig,
        *,
        client: VolcengineCdnClientProtocol | None = None,
        models: Any | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._client = client
        self._models = models

    def _ensure_client(self) -> VolcengineCdnClientProtocol:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @staticmethod
    def _build_client(config: CdnConfig) -> VolcengineCdnClientProtocol:
        access_key = config.access_key_id.resolve(f"CDN access key for {config.name}")
        secret_key = config.secret_access_key.resolve(f"CDN secret key for {config.name}")
        import volcenginesdkcdn
        import volcenginesdkcore

        sdk_config: Any = volcenginesdkcore.Configuration()
        sdk_config.ak = access_key
        sdk_config.sk = secret_key
        sdk_config.region = "cn-north-1"
        sdk_config.host = "cdn.volcengineapi.com"
        sdk_config.scheme = "https"
        return volcenginesdkcdn.CDNApi(volcenginesdkcore.ApiClient(sdk_config))

    def _require_models(self) -> Any:
        if self._models is not None:
            return self._models
        import volcenginesdkcdn

        return volcenginesdkcdn

    def build_url(self, object_key: str) -> str:
        return build_cdn_url(self.config.base_url, object_key)

    def purge_url(self, urls: list[str]) -> CdnTaskResult:
        targets = list(urls)
        try:
            request = self._require_models().SubmitRefreshTaskRequest(type="file", url_list=targets)
            response = self._ensure_client().submit_refresh_task(request)
        except Exception as exc:
            return self._failed("purge_url", targets, exc)
        return self._submitted("purge_url", targets, response)

    def purge_path(
        self,
        paths: list[str],
        *,
        flush_type: Literal["flush", "delete"],
    ) -> CdnTaskResult:
        targets = [path if path.endswith("/") else f"{path}/" for path in paths]
        try:
            request = self._require_models().SubmitRefreshTaskRequest(
                type="dir",
                url_list=targets,
                prefix=False,
                delete=flush_type == "delete",
            )
            response = self._ensure_client().submit_refresh_task(request)
        except Exception as exc:
            return self._failed("purge_path", targets, exc)
        return self._submitted("purge_path", targets, response)

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult:
        if area:
            raise ConfigurationError(
                "Volcengine CDN prefetch does not support the Tencent-specific --area option"
            )
        targets = list(urls)
        try:
            request = self._require_models().SubmitPreloadTaskRequest(url_list=targets)
            response = self._ensure_client().submit_preload_task(request)
        except Exception as exc:
            return self._failed("prefetch", targets, exc)
        return self._submitted("prefetch", targets, response)

    def status(self, task_id: str) -> CdnTaskResult:
        task_type = self._task_type(task_id)
        try:
            request = self._require_models().DescribeContentTasksRequest(
                task_id=task_id,
                task_type=task_type,
            )
            response = self._ensure_client().describe_content_tasks(request)
        except Exception as exc:
            return self._failed("status", [], exc, task_id=task_id)
        items = list(getattr(response, "data", None) or [])
        targets = [str(getattr(item, "url", "") or "") for item in items]
        targets = [target for target in targets if target]
        if not items:
            return CdnTaskResult("status", "not_found", task_id, targets)
        statuses = {str(getattr(item, "status", "") or "").lower() for item in items}
        if "failed" in statuses:
            remarks = [str(getattr(item, "remark", "") or "") for item in items]
            error = "; ".join(remark for remark in remarks if remark)
            return CdnTaskResult("status", "failed", task_id, targets, error)
        if statuses == {"complete"}:
            return CdnTaskResult("status", "completed", task_id, targets)
        return CdnTaskResult("status", "in_progress", task_id, targets)

    @staticmethod
    def _task_type(task_id: str) -> str:
        if task_id.startswith("refresh_url_"):
            return "refresh_file"
        if task_id.startswith("refresh_dir_"):
            return "refresh_dir"
        if task_id.startswith("refresh_regex_"):
            return "refresh_regex"
        if task_id.startswith("prefetch_url_"):
            return "preload"
        raise ConfigurationError(f"Unrecognized Volcengine CDN task ID: {task_id}")

    @staticmethod
    def _submitted(operation: str, targets: list[str], response: Any) -> CdnTaskResult:
        return CdnTaskResult(
            operation=operation,
            status="submitted",
            task_id=str(getattr(response, "task_id", "") or ""),
            targets=targets,
        )

    @classmethod
    def _failed(
        cls,
        operation: str,
        targets: list[str],
        exc: Exception,
        *,
        task_id: str = "",
    ) -> CdnTaskResult:
        return CdnTaskResult(
            operation=operation,
            status="failed",
            task_id=task_id,
            targets=targets,
            error=cls._safe_error(exc),
        )

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        body = getattr(exc, "body", None)
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        if isinstance(body, str):
            try:
                payload = json.loads(body)
                metadata = payload.get("ResponseMetadata", {})
                error = metadata.get("Error", {})
                code = str(error.get("Code", "") or "")
                message = str(error.get("Message", "") or "")
                request_id = str(metadata.get("RequestId", "") or "")
                parts = [part for part in (code, message, request_id) if part]
                if parts:
                    return " | ".join(parts)
            except (TypeError, ValueError, AttributeError):
                pass
        return type(exc).__name__
