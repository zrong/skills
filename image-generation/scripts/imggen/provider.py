"""Backward-compatible Python helpers backed by the strict adapter registry."""

from __future__ import annotations

from imggen.adapters import create_adapter
from imggen.models import EndpointConfig, ImageArtifact, ImageRequest
from imggen.service import execute


def fetch_models(endpoint: EndpointConfig) -> list[str]:
    return create_adapter(endpoint).list_models()


def run_request(
    endpoint: EndpointConfig, request: ImageRequest, max_attempts: int = 3
) -> list[ImageArtifact]:
    return execute(endpoint, request, max_attempts)
