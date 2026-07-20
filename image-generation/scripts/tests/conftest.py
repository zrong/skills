from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "agent_config.toml"
    path.write_text(
        """
[image-generation]
default_provider = "test"

[image-generation.providers.test]
name = "Test"
default_endpoint = "openai"

[image-generation.providers.test.endpoints.openai]
adapter = "openai"
base_url = "https://example.test/v1"
api_key = "test-openai"
default_model = "gpt-image-test"

[image-generation.providers.test.endpoints.openai.models."gpt-image-test"]
adapter = "openai"
operations = ["generate", "edit"]
capabilities = ["multi_reference", "mask", "size", "quality", "output_format"]
sizes = ["1024x1024"]
qualities = ["high"]
output_formats = ["png"]
max_references = 2
max_outputs = 2

[image-generation.providers.test.endpoints.gemini]
adapter = "gemini"
base_url = "https://example.test/v1beta"
api_key = "test-gemini"
default_model = "gemini-image-test"

[image-generation.providers.test.endpoints.gemini.models."gemini-image-test"]
adapter = "gemini"
operations = ["generate", "edit"]
capabilities = ["multi_reference", "aspect_ratio", "image_size"]
max_references = 3
max_outputs = 2

[image-generation.providers.test.endpoints.seedream]
adapter = "seedream"
base_url = "https://example.test/v3"
api_key = "test-seedream"
default_model = "doubao-seedream-5-0-pro-test"

[image-generation.providers.test.endpoints.seedream.models."doubao-seedream-5-0-pro-test"]
adapter = "seedream"
operations = ["generate", "edit"]
capabilities = ["multi_reference", "size", "seed", "watermark", "interactive_edit"]
max_references = 10
max_outputs = 1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path
