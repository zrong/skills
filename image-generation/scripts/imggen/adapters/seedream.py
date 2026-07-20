"""Volcengine Ark Seedream Images API adapter."""

from __future__ import annotations

import json
from typing import Any

from imggen.adapters.base import AdapterResponseError, ImageAdapter, data_url
from imggen.models import ImageArtifact, ImageRequest


class SeedreamAdapter(ImageAdapter):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.endpoint.api_key}",
            **self.endpoint.headers,
        }

    def list_models(self) -> list[str]:
        path = str(
            next(iter(self.endpoint.models.values())).options.get(
                "models_path", "/models"
            )
        )
        with self._client() as client:
            response = client.get(
                f"{self.endpoint.base_url}{path}", headers=self._headers()
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
            return sorted(str(row["id"]) for row in rows if row.get("id"))

    def execute(self, request: ImageRequest) -> list[ImageArtifact]:
        options = request.model.options
        body: dict[str, Any] = {
            "model": request.model.api_model,
            "prompt": request.prompt,
            "response_format": str(options.get("response_format", "b64_json")),
            **dict(options.get("payload", {})),
        }
        if request.references:
            encoded = [data_url(path) for path in request.references]
            image_field = str(options.get("image_field", "image"))
            body[image_field] = encoded if len(encoded) > 1 else encoded[0]
        if request.size:
            body["size"] = request.size
        if request.seed is not None:
            body["seed"] = request.seed
        if request.watermark is not None:
            body["watermark"] = request.watermark
        if request.stream is not None:
            body["stream"] = request.stream
        if request.n > 1 or request.sequential:
            body["sequential_image_generation"] = request.sequential or "auto"
            body["sequential_image_generation_options"] = {"max_images": request.n}
        path = str(options.get("generate_path", "/images/generations"))
        with self._client() as client:
            response = client.post(
                f"{self.endpoint.base_url}{path}", headers=self._headers(), json=body
            )
            response.raise_for_status()
            if request.stream:
                rows = self._stream_rows(response.text)
            else:
                rows = response.json().get("data", [])
            if not rows:
                raise AdapterResponseError("Seedream 响应中没有图片 data")
            return [self._download_or_decode(row, client) for row in rows]

    @staticmethod
    def _stream_rows(body: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            event = json.loads(value)
            if isinstance(event.get("data"), list):
                rows.extend(event["data"])
            elif any(key in event for key in ("url", "b64_json", "b64")):
                rows.append(event)
        return rows
