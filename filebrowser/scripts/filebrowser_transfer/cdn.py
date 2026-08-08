"""CDN cache management adapters: purge URL, purge directory, prefetch."""

from __future__ import annotations

from typing import Any, Literal, Protocol
from urllib.parse import quote

from .models import (
    CdnTargetConfig,
    CdnTaskResult,
    ConfigurationError,
    S3TargetConfig,
)


class CdnClientProtocol(Protocol):
    """Subset of the Tencent Cloud ``cdn_client`` surface we depend on."""

    def PurgeUrlsCache(self, request: Any) -> Any: ...

    def PurgePathCache(self, request: Any) -> Any: ...

    def PushUrlsCache(self, request: Any) -> Any: ...


class CdnCacheManager(Protocol):
    name: str

    def purge_url(self, urls: list[str]) -> CdnTaskResult: ...

    def purge_path(
        self,
        paths: list[str],
        *,
        flush_type: Literal["flush", "delete"],
    ) -> CdnTaskResult: ...

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult: ...

    def build_url(self, object_key: str) -> str: ...


def build_cdn_url(base_url: str, object_key: str) -> str:
    """Join a CDN base URL with a relative object key."""
    return f"{base_url.rstrip('/')}/{quote(object_key, safe='/')}"


class TencentCdnCacheManager:
    """Tencent Cloud CDN cache manager.

    Wraps ``PurgeUrlsCache`` / ``PurgePathCache`` / ``PushUrlsCache``. The
    Tencent Cloud SDK is imported lazily so targets without a CDN subtable pay
    no import cost. ``client`` and ``models`` are injection points for tests.
    """

    def __init__(
        self,
        config: CdnTargetConfig,
        *,
        client: CdnClientProtocol | None = None,
        models: Any | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._models = models
        self._client: CdnClientProtocol | None = client

    def _ensure_client(self) -> CdnClientProtocol:
        if self._client is None:
            self._client = self._build_client(self.config)
        return self._client

    @staticmethod
    def _build_client(config: CdnTargetConfig) -> CdnClientProtocol:
        secret_id = config.access_key_id.resolve(f"CDN access key for {config.name}")
        secret_key = config.secret_access_key.resolve(
            f"CDN secret key for {config.name}"
        )
        from tencentcloud.cdn.v20180606 import cdn_client  # type: ignore[import-not-found]
        from tencentcloud.common import credential  # type: ignore[import-not-found]

        return cdn_client.CdnClient(credential.Credential(secret_id, secret_key), "")

    def build_url(self, object_key: str) -> str:
        return build_cdn_url(self.config.base_url, object_key)

    def purge_url(self, urls: list[str]) -> CdnTaskResult:
        targets = list(urls)
        try:
            models = self._require_models()
            request = models.PurgeUrlsCacheRequest()
            request.Urls = targets
            response = self._ensure_client().PurgeUrlsCache(request)
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
            models = self._require_models()
            request = models.PurgePathCacheRequest()
            request.Paths = targets
            request.FlushType = flush_type
            response = self._ensure_client().PurgePathCache(request)
        except Exception as exc:
            return self._failed("purge_path", targets, exc)
        return self._submitted("purge_path", targets, response)

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult:
        targets = list(urls)
        try:
            models = self._require_models()
            request = models.PushUrlsCacheRequest()
            request.Urls = targets
            if area:
                request.Area = area
            response = self._ensure_client().PushUrlsCache(request)
        except Exception as exc:
            return self._failed("prefetch", targets, exc)
        return self._submitted("prefetch", targets, response)

    def _require_models(self) -> Any:
        if self._models is not None:
            return self._models
        from tencentcloud.cdn.v20180606 import models  # type: ignore[import-not-found]

        return models

    @staticmethod
    def _submitted(operation: str, targets: list[str], response: Any) -> CdnTaskResult:
        task_id = str(getattr(response, "TaskId", "") or "")
        return CdnTaskResult(
            operation=operation,
            status="submitted",
            task_id=task_id,
            targets=targets,
            error="",
        )

    @staticmethod
    def _failed(operation: str, targets: list[str], exc: Exception) -> CdnTaskResult:
        return CdnTaskResult(
            operation=operation,
            status="failed",
            task_id="",
            targets=targets,
            error=str(exc),
        )


def build_cdn_cache_manager(target_config: S3TargetConfig) -> CdnCacheManager | None:
    """Build a CDN manager for the target, or ``None`` when CDN is unconfigured."""
    cdn = target_config.cdn
    if cdn is None:
        return None
    if cdn.provider != "tencent":
        raise ConfigurationError(f"Unsupported CDN provider: {cdn.provider}")
    return TencentCdnCacheManager(cdn)
