"""HTTP client for matting-api."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import MattingConfig
from .errors import ApiError, ServiceUnavailableError


class MattingClient:
    def __init__(
        self, config: MattingConfig, *, transport: httpx.BaseTransport | None = None
    ):
        self.config = config
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout,
            headers=self.config.headers,
            transport=self.transport,
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        try:
            with self._client() as client:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ServiceUnavailableError(
                f"matting-api 不可达: {self.config.base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"matting-api HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(f"matting-api 返回无效 JSON: {path}") from exc
        return self._unwrap(payload, path)

    @staticmethod
    def _unwrap(payload: Any, path: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiError(f"matting-api 返回非对象: {path}")
        if "success" in payload:
            if payload.get("success") is not True:
                error = (
                    payload.get("error")
                    if isinstance(payload.get("error"), dict)
                    else {}
                )
                error_type = str(error.get("type") or "ApiError")
                raise ApiError(
                    f"{error_type}: {payload.get('message') or 'matting-api 业务失败'}"
                )
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ApiError(f"matting-api 成功响应缺少对象 data: {path}")
            return data
        return payload

    def status(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/status")

    def capabilities(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/capabilities")

    def submit(
        self,
        input_path: Path,
        *,
        method: str,
        model: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        data = {
            "method": method,
            "model_key": model,
            "parameters": json.dumps(
                parameters, ensure_ascii=False, separators=(",", ":")
            ),
        }
        with input_path.open("rb") as source:
            files = {"image": (input_path.name, source, "image/png")}
            return self._request_json(
                "POST", "/api/matting/generate", data=data, files=files
            )

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/matting/tasks/{task_id}")

    def download(self, task_id: str) -> bytes:
        path = f"/api/matting/download/{task_id}"
        url = f"{self.config.base_url}{path}"
        try:
            with self._client() as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ApiError(f"下载 matting 结果失败: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"下载 matting 结果 HTTP {exc.response.status_code}"
            ) from exc
