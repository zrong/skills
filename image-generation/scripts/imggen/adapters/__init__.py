"""Adapter registry. Adapter choice comes only from endpoint configuration."""

from __future__ import annotations

from imggen.adapters.base import ImageAdapter
from imggen.adapters.gemini import GeminiAdapter
from imggen.adapters.openai import OpenAIAdapter
from imggen.adapters.seedream import SeedreamAdapter
from imggen.models import ConfigError, EndpointConfig


_ADAPTERS: dict[str, type[ImageAdapter]] = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "seedream": SeedreamAdapter,
}


def create_adapter(endpoint: EndpointConfig) -> ImageAdapter:
    adapter_class = _ADAPTERS.get(endpoint.adapter)
    if adapter_class is None:
        raise ConfigError(f"未知 adapter: {endpoint.adapter}")
    return adapter_class(endpoint)
