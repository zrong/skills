"""Native Gemini generateContent image adapter."""

from __future__ import annotations

import base64
from typing import Any

from imggen.adapters.base import AdapterResponseError, ImageAdapter, mime_for_path
from imggen.models import ImageArtifact, ImageRequest


class GeminiAdapter(ImageAdapter):
    def _headers(self) -> dict[str, str]:
        auth = (
            {"Authorization": f"Bearer {self.endpoint.api_key}"}
            if self.endpoint.auth == "bearer"
            else {"x-goog-api-key": self.endpoint.api_key}
        )
        return {**auth, **self.endpoint.headers}

    def list_models(self) -> list[str]:
        with self._client() as client:
            response = client.get(
                f"{self.endpoint.base_url}/models", headers=self._headers()
            )
            response.raise_for_status()
            rows = response.json().get("models", [])
            return sorted(
                str(row.get("name", "")).removeprefix("models/")
                for row in rows
                if row.get("name")
            )

    def execute(self, request: ImageRequest) -> list[ImageArtifact]:
        parts: list[dict[str, Any]] = []
        for image in request.references:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_for_path(image),
                        "data": base64.b64encode(image.read_bytes()).decode("ascii"),
                    }
                }
            )
        parts.append({"text": request.prompt})
        generation: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config: dict[str, str] = {}
        if request.aspect_ratio:
            image_config["aspectRatio"] = request.aspect_ratio
        if request.image_size:
            image_config["imageSize"] = request.image_size
        if image_config:
            generation["imageConfig"] = image_config
        generation.update(dict(request.model.options.get("generation_config", {})))
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation,
        }
        path = str(request.model.options.get("generate_path", ""))
        if not path:
            path = f"/models/{request.model.api_model}:generateContent"
        artifacts: list[ImageArtifact] = []
        with self._client() as client:
            for _ in range(request.n):
                response = client.post(
                    f"{self.endpoint.base_url}{path}",
                    headers=self._headers(),
                    json=body,
                )
                response.raise_for_status()
                candidates = response.json().get("candidates", [])
                if not candidates:
                    raise AdapterResponseError("Gemini 响应中没有 candidates")
                for part in candidates[0].get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        artifacts.append(
                            ImageArtifact(
                                base64.b64decode(inline["data"]),
                                str(
                                    inline.get("mimeType")
                                    or inline.get("mime_type")
                                    or "image/png"
                                ),
                            )
                        )
            if not artifacts:
                raise AdapterResponseError("Gemini 响应中没有图片数据")
        return artifacts
