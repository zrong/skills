from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from filebrowser_transfer.cdn import (
    TencentCdnCacheManager,
    build_cdn_cache_manager,
    build_cdn_url,
)
from filebrowser_transfer.models import (
    CdnTargetConfig,
    ConfigurationError,
    S3TargetConfig,
    SecretValue,
)


class _FakeRequest:
    def __init__(self) -> None:
        self.Urls: Any = None
        self.Paths: Any = None
        self.FlushType: Any = None
        self.Area: Any = None


class _FakeModels:
    PurgeUrlsCacheRequest = _FakeRequest
    PurgePathCacheRequest = _FakeRequest
    PushUrlsCacheRequest = _FakeRequest


class _FakeCdnClient:
    def __init__(self, *, task_id: str = "task-123", error: Exception | None = None) -> None:
        self.task_id = task_id
        self.error = error
        self.calls: list[tuple[str, _FakeRequest]] = []

    def PurgeUrlsCache(self, request: _FakeRequest) -> Any:
        self.calls.append(("PurgeUrlsCache", request))
        if self.error:
            raise self.error
        return SimpleNamespace(TaskId=self.task_id)

    def PurgePathCache(self, request: _FakeRequest) -> Any:
        self.calls.append(("PurgePathCache", request))
        if self.error:
            raise self.error
        return SimpleNamespace(TaskId=self.task_id)

    def PushUrlsCache(self, request: _FakeRequest) -> Any:
        self.calls.append(("PushUrlsCache", request))
        if self.error:
            raise self.error
        return SimpleNamespace(TaskId=self.task_id)


def _config(**overrides: Any) -> CdnTargetConfig:
    base: dict[str, Any] = {
        "name": "archive",
        "provider": "tencent",
        "base_url": "https://cdn.example.test",
        "access_key_id": SecretValue(direct="ak"),
        "secret_access_key": SecretValue(direct="sk"),
    }
    base.update(overrides)
    return CdnTargetConfig(**base)


def _manager(**overrides: Any) -> tuple[TencentCdnCacheManager, _FakeCdnClient]:
    client = _FakeCdnClient()
    manager = TencentCdnCacheManager(
        _config(**overrides), client=client, models=_FakeModels
    )
    return manager, client


def test_build_cdn_url_quotes_object_key() -> None:
    assert build_cdn_url("https://cdn.example.test", "a/b c.jpg") == (
        "https://cdn.example.test/a/b%20c.jpg"
    )
    assert build_cdn_url("https://cdn.example.test/", "x") == (
        "https://cdn.example.test/x"
    )


def test_purge_url_sets_urls_and_returns_task_id() -> None:
    manager, client = _manager()
    result = manager.purge_url(
        ["https://cdn.example.test/a", "https://cdn.example.test/b"]
    )

    assert result.operation == "purge_url"
    assert result.status == "submitted"
    assert result.task_id == "task-123"
    assert result.targets == [
        "https://cdn.example.test/a",
        "https://cdn.example.test/b",
    ]
    assert result.error == ""
    name, request = client.calls[0]
    assert name == "PurgeUrlsCache"
    assert request.Urls == [
        "https://cdn.example.test/a",
        "https://cdn.example.test/b",
    ]


def test_purge_path_normalizes_trailing_slash_and_sets_fields() -> None:
    manager, client = _manager()
    result = manager.purge_path(
        ["https://cdn.example.test/dir", "https://cdn.example.test/keep/"],
        flush_type="delete",
    )

    assert result.status == "submitted"
    assert result.targets == [
        "https://cdn.example.test/dir/",
        "https://cdn.example.test/keep/",
    ]
    name, request = client.calls[0]
    assert name == "PurgePathCache"
    assert request.Paths == [
        "https://cdn.example.test/dir/",
        "https://cdn.example.test/keep/",
    ]
    assert request.FlushType == "delete"


def test_prefetch_sets_urls_and_optional_area() -> None:
    manager, client = _manager()
    result = manager.prefetch(["https://cdn.example.test/a"], area="mainland")

    assert result.status == "submitted"
    name, request = client.calls[0]
    assert name == "PushUrlsCache"
    assert request.Urls == ["https://cdn.example.test/a"]
    assert request.Area == "mainland"


def test_prefetch_omits_area_when_empty() -> None:
    manager, client = _manager()
    manager.prefetch(["https://cdn.example.test/a"], area="")

    request = client.calls[0][1]
    assert request.Area is None


def test_failure_is_captured_without_raising() -> None:
    client = _FakeCdnClient(error=RuntimeError("boom"))
    manager = TencentCdnCacheManager(_config(), client=client, models=_FakeModels)
    result = manager.purge_url(["https://cdn.example.test/a"])

    assert result.status == "failed"
    assert result.task_id == ""
    assert "boom" in result.error
    assert result.targets == ["https://cdn.example.test/a"]


def test_build_cdn_cache_manager_returns_none_without_cdn() -> None:
    target = S3TargetConfig(name="archive", bucket="b")
    assert build_cdn_cache_manager(target) is None


def test_build_cdn_cache_manager_rejects_unknown_provider() -> None:
    target = S3TargetConfig(name="archive", bucket="b", cdn=_config(provider="aws"))
    with pytest.raises(ConfigurationError, match="Unsupported CDN provider"):
        build_cdn_cache_manager(target)
