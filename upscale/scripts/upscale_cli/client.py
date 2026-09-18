from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import UpscaleConfig
from .errors import ApiError, ServiceUnavailableError


class UpscaleClient:
    def __init__(
        self, config: UpscaleConfig, *, transport: httpx.BaseTransport | None = None
    ):
        self.config = config
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout,
            headers=self.config.headers,
            transport=self.transport,
            follow_redirects=True,
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        try:
            with self._client() as client:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ServiceUnavailableError(
                f"upscale-api 不可达: {self.config.base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"upscale-api HTTP {exc.response.status_code}: {_safe_error(exc.response)}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(f"upscale-api 返回无效 JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise ApiError(f"upscale-api 返回非对象: {path}")
        return payload

    def health(self) -> dict[str, Any]:
        return self._request_json("GET", "/health")

    def status(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/status")

    def capabilities(self) -> dict[str, Any]:
        return self._request_json("GET", "/api/capabilities")

    def submit(
        self,
        input_path: Path,
        *,
        media_type: str,
        model: str,
        mode: str,
        scale: float | None,
        target_width: int | None,
        target_height: int | None,
        fit: str,
        start: float,
        duration: float,
        target_fps: str | None,
        interpolation_model: str | None,
    ) -> dict[str, Any]:
        data: dict[str, str] = {"media_type": media_type, "model": model}
        if media_type == "image":
            data["scale"] = str(scale if scale is not None else 2)
        else:
            data.update(mode=mode, start=str(start), duration=str(duration))
            for key, value in (("scale", scale), ("target_width", target_width),
                               ("target_height", target_height)):
                if value is not None:
                    data[key] = str(value)
            data["fit"] = fit
            if target_fps is not None:
                data["target_fps"] = target_fps
            if interpolation_model is not None:
                data["interpolation_model"] = interpolation_model
        with input_path.open("rb") as source:
            files = {"file": (input_path.name, source, "application/octet-stream")}
            return self._request_json(
                "POST", "/api/tasks/upload", data=data, files=files
            )

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/tasks/{task_id}")

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/api/tasks/{task_id}/cancel")

    def download_url(self, task_id: str) -> str:
        return f"{self.config.base_url}/api/tasks/{task_id}/download"

    def download_to(self, task_id: str, destination: Path) -> int:
        url = self.download_url(task_id)
        written = 0
        try:
            with self._client() as client, client.stream("GET", url) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        output.write(chunk)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            destination.unlink(missing_ok=True)
            raise ApiError(f"下载 upscale 结果失败: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            destination.unlink(missing_ok=True)
            raise ApiError(
                f"下载 upscale 结果 HTTP {exc.response.status_code}"
            ) from exc
        if written <= 0:
            destination.unlink(missing_ok=True)
            raise ApiError("upscale-api 下载结果为空")
        return written


def _safe_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error")
        if isinstance(detail, (str, int, float)):
            return str(detail)[:500]
    return response.reason_phrase
