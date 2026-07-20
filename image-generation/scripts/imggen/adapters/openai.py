"""OpenAI Images API adapter."""

from __future__ import annotations

from typing import Any

from imggen.adapters.base import (
    AdapterResponseError,
    ImageAdapter,
    mime_for_path,
    optional_payload,
)
from imggen.models import ImageArtifact, ImageRequest


class OpenAIAdapter(ImageAdapter):
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.endpoint.api_key}",
            **self.endpoint.headers,
        }

    def list_models(self) -> list[str]:
        with self._client() as client:
            response = client.get(
                f"{self.endpoint.base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
            return sorted(
                str(item["id"])
                for item in response.json().get("data", [])
                if item.get("id")
            )

    def execute(self, request: ImageRequest) -> list[ImageArtifact]:
        if request.operation == "edit":
            return self._edit(request)
        return self._generate(request)

    def _generate(self, request: ImageRequest) -> list[ImageArtifact]:
        options = request.model.options
        path = str(options.get("generate_path", "/images/generations"))
        body: dict[str, Any] = {
            "model": request.model.api_model,
            "prompt": request.prompt,
            "n": request.n,
            **optional_payload(request),
            **dict(options.get("payload", {})),
        }
        field_map = dict(options.get("field_map", {}))
        body = {str(field_map.get(key, key)): value for key, value in body.items()}
        with self._client() as client:
            response = client.post(
                f"{self.endpoint.base_url}{path}", headers=self._headers(), json=body
            )
            response.raise_for_status()
            rows = response.json().get(str(options.get("response_list", "data")), [])
            if not rows:
                raise AdapterResponseError("OpenAI Images 响应中没有 data")
            return [self._download_or_decode(row, client) for row in rows]

    def _edit(self, request: ImageRequest) -> list[ImageArtifact]:
        options = request.model.options
        path = str(options.get("edit_path", "/images/edits"))
        image_field = str(options.get("image_field", "image[]"))
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            (image_field, (path.name, path.read_bytes(), mime_for_path(path)))
            for path in request.references
        ]
        if request.mask:
            files.append(
                (
                    "mask",
                    (
                        request.mask.name,
                        request.mask.read_bytes(),
                        mime_for_path(request.mask),
                    ),
                )
            )
        data: dict[str, str] = {
            "model": request.model.api_model,
            "prompt": request.prompt,
            "n": str(request.n),
        }
        for key, value in optional_payload(request).items():
            data[key] = str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in dict(options.get("payload", {})).items():
            data[str(key)] = str(value)
        with self._client() as client:
            response = client.post(
                f"{self.endpoint.base_url}{path}",
                headers=self._headers(),
                data=data,
                files=files,
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
            if not rows:
                raise AdapterResponseError("OpenAI Images edit 响应中没有 data")
            return [self._download_or_decode(row, client) for row in rows]
