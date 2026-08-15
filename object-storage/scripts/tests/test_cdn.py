from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from object_storage.cdn import TencentCdnCacheManager, build_cdn_url
from object_storage.models import CdnConfig, SecretValue


class FakeRequest:
    def __init__(self) -> None:
        self.Urls: Any = None
        self.Paths: Any = None
        self.FlushType: Any = None
        self.Area: Any = None


class FakeModels:
    PurgeUrlsCacheRequest = FakeRequest
    PurgePathCacheRequest = FakeRequest
    PushUrlsCacheRequest = FakeRequest


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, FakeRequest]] = []

    def PurgeUrlsCache(self, request: FakeRequest) -> Any:
        self.calls.append(("url", request))
        return SimpleNamespace(TaskId="task-1")

    def PurgePathCache(self, request: FakeRequest) -> Any:
        self.calls.append(("path", request))
        return SimpleNamespace(TaskId="task-2")

    def PushUrlsCache(self, request: FakeRequest) -> Any:
        self.calls.append(("prefetch", request))
        return SimpleNamespace(TaskId="task-3")


def _manager() -> tuple[TencentCdnCacheManager, FakeClient]:
    client = FakeClient()
    manager = TencentCdnCacheManager(
        CdnConfig(
            name="archive",
            provider="tencent",
            base_url="https://cdn.example.test",
            access_key_id=SecretValue(direct="ak"),
            secret_access_key=SecretValue(direct="sk"),
        ),
        client=client,
        models=FakeModels,
    )
    return manager, client


def test_build_url_quotes_key() -> None:
    assert build_cdn_url("https://cdn.example.test", "a/b c.jpg") == (
        "https://cdn.example.test/a/b%20c.jpg"
    )


def test_purge_path_and_prefetch() -> None:
    manager, client = _manager()
    path_result = manager.purge_path(["https://cdn.example.test/dir"], flush_type="delete")
    prefetch_result = manager.prefetch(["https://cdn.example.test/a"], area="mainland")

    assert path_result.targets == ["https://cdn.example.test/dir/"]
    assert client.calls[0][1].FlushType == "delete"
    assert prefetch_result.task_id == "task-3"
    assert client.calls[1][1].Area == "mainland"
