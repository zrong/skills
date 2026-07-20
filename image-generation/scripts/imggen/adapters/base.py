"""Shared adapter utilities."""

from __future__ import annotations

import base64
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from imggen.models import EndpointConfig, ImageArtifact, ImageRequest, ImggenError


class AdapterResponseError(ImggenError):
    """An endpoint returned a successful but unusable response."""


def mime_for_path(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "image/png"


def data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_for_path(path)};base64,{encoded}"


def decode_data_url(value: str) -> tuple[bytes, str]:
    header, encoded = value.split(",", 1)
    mime = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/png"
    return base64.b64decode(encoded), mime


class ImageAdapter(ABC):
    def __init__(self, endpoint: EndpointConfig):
        self.endpoint = endpoint

    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: ImageRequest) -> list[ImageArtifact]:
        raise NotImplementedError

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.endpoint.timeout, follow_redirects=True)

    def _download_or_decode(
        self, item: dict[str, Any], client: httpx.Client
    ) -> ImageArtifact:
        encoded = item.get("b64_json") or item.get("b64") or item.get("base64")
        if encoded:
            return ImageArtifact(
                base64.b64decode(encoded),
                str(item.get("mime_type") or item.get("mimeType") or "image/png"),
                item.get("revised_prompt"),
            )
        url = str(item.get("url") or item.get("image_url") or "")
        if url.startswith("data:"):
            raw, mime = decode_data_url(url)
            return ImageArtifact(raw, mime, item.get("revised_prompt"))
        if url:
            response = client.get(url)
            response.raise_for_status()
            mime = response.headers.get("content-type", "image/png").split(";", 1)[0]
            return ImageArtifact(response.content, mime, item.get("revised_prompt"))
        raise AdapterResponseError("图片响应中没有 base64 或 URL")


def optional_payload(request: ImageRequest) -> dict[str, Any]:
    """Return only explicitly supplied cross-provider image options."""
    fields = (
        "size",
        "quality",
        "background",
        "output_format",
        "output_compression",
        "moderation",
        "input_fidelity",
        "seed",
        "watermark",
    )
    return {
        name: getattr(request, name)
        for name in fields
        if getattr(request, name) is not None
    }
