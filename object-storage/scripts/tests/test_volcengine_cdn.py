from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from object_storage.models import CdnConfig, ConfigurationError, SecretValue
from object_storage.volcengine_cdn import VolcengineCdnCacheManager


class FakeRequest:
    def __init__(self, **kwargs: Any) -> None:
        self.values = kwargs


class FakeModels:
    SubmitRefreshTaskRequest = FakeRequest
    SubmitPreloadTaskRequest = FakeRequest
    DescribeContentTasksRequest = FakeRequest


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, FakeRequest]] = []
        self.status_items: list[Any] = []

    def submit_refresh_task(self, body: FakeRequest) -> Any:
        self.calls.append(("refresh", body))
        prefix = "refresh_dir" if body.values["type"] == "dir" else "refresh_url"
        return SimpleNamespace(task_id=f"{prefix}_1")

    def submit_preload_task(self, body: FakeRequest) -> Any:
        self.calls.append(("preload", body))
        return SimpleNamespace(task_id="prefetch_url_1")

    def describe_content_tasks(self, body: FakeRequest) -> Any:
        self.calls.append(("status", body))
        return SimpleNamespace(data=self.status_items)


def _manager() -> tuple[VolcengineCdnCacheManager, FakeClient]:
    client = FakeClient()
    manager = VolcengineCdnCacheManager(
        CdnConfig(
            name="tos",
            provider="volcengine",
            base_url="https://cdn.example.test",
            access_key_id=SecretValue(direct="ak"),
            secret_access_key=SecretValue(direct="sk"),
        ),
        client=client,
        models=FakeModels,
    )
    return manager, client


def test_refresh_file_and_directory() -> None:
    manager, client = _manager()

    file_result = manager.purge_url(["https://cdn.example.test/a.jpg"])
    directory_result = manager.purge_path(["https://cdn.example.test/releases"], flush_type="flush")

    assert file_result.task_id == "refresh_url_1"
    assert client.calls[0][1].values["type"] == "file"
    assert directory_result.targets == ["https://cdn.example.test/releases/"]
    assert client.calls[1][1].values["type"] == "dir"
    assert client.calls[1][1].values["prefix"] is False
    assert client.calls[1][1].values["delete"] is False


def test_directory_delete_and_prefetch() -> None:
    manager, client = _manager()

    manager.purge_path(["https://cdn.example.test/releases/"], flush_type="delete")
    prefetch_result = manager.prefetch(["https://cdn.example.test/a.jpg"])

    assert client.calls[0][1].values["delete"] is True
    assert prefetch_result.task_id == "prefetch_url_1"
    assert client.calls[1][1].values["url_list"] == ["https://cdn.example.test/a.jpg"]
    with pytest.raises(ConfigurationError, match="--area"):
        manager.prefetch(["https://cdn.example.test/a.jpg"], area="mainland")


def test_status_maps_completed_and_failed_tasks() -> None:
    manager, client = _manager()
    client.status_items = [
        SimpleNamespace(status="complete", url="https://cdn.example.test/a.jpg", remark="")
    ]
    completed = manager.status("refresh_url_1")

    assert completed.status == "completed"
    assert client.calls[0][1].values["task_type"] == "refresh_file"

    client.status_items = [
        SimpleNamespace(status="failed", url="https://cdn.example.test/a.jpg", remark="denied")
    ]
    failed = manager.status("refresh_url_1")
    assert failed.status == "failed"
    assert failed.error == "denied"


def test_status_rejects_unknown_task_id() -> None:
    manager, _ = _manager()
    with pytest.raises(ConfigurationError, match="Unrecognized"):
        manager.status("other_1")
