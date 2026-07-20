from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx

from imggen.adapters.gemini import GeminiAdapter
from imggen.adapters.openai import OpenAIAdapter
from imggen.adapters.seedream import SeedreamAdapter
from imggen.config import get_endpoint_config
from imggen.models import ImageRequest


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_openai_generation_payload(config_file: Path, monkeypatch) -> None:
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    adapter = OpenAIAdapter(endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/images/generations"
        assert body == {
            "model": "gpt-image-test",
            "prompt": "draw",
            "n": 1,
            "size": "1024x1024",
            "quality": "high",
            "background": "transparent",
            "output_format": "png",
            "output_compression": 75,
            "moderation": "low",
        }
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(b"openai").decode()}]}
        )

    monkeypatch.setattr(adapter, "_client", lambda: _mock_client(handler))
    policy = endpoint.resolve_model(None, "generate")
    result = adapter.execute(
        ImageRequest(
            operation="generate",
            prompt="draw",
            model=policy,
            size="1024x1024",
            quality="high",
            background="transparent",
            output_format="png",
            output_compression=75,
            moderation="low",
        )
    )
    assert result[0].data == b"openai"


def test_openai_multipart_multi_reference_and_mask(
    config_file: Path, tmp_path: Path, monkeypatch
) -> None:
    references = [tmp_path / "one.png", tmp_path / "two.png"]
    mask = tmp_path / "mask.png"
    for path in (*references, mask):
        path.write_bytes(b"image-bytes")
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    adapter = OpenAIAdapter(endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/edits"
        assert request.headers["content-type"].startswith("multipart/form-data")
        body = request.content
        assert body.count(b'name="image[]"') == 2
        assert body.count(b'name="mask"') == 1
        assert b'name="model"' in body and b"gpt-image-test" in body
        assert b'name="input_fidelity"' in body and b"high" in body
        encoded = base64.b64encode(b"edited").decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(adapter, "_client", lambda: _mock_client(handler))
    result = adapter.execute(
        ImageRequest(
            operation="edit",
            prompt="edit",
            model=endpoint.resolve_model(None, "edit"),
            references=references,
            mask=mask,
            input_fidelity="high",
        )
    )
    assert result[0].data == b"edited"


def test_gemini_semantic_multi_image_edit(
    config_file: Path, tmp_path: Path, monkeypatch
) -> None:
    refs = [tmp_path / "a.png", tmp_path / "b.png"]
    for index, path in enumerate(refs):
        path.write_bytes(f"image-{index}".encode())
    endpoint = get_endpoint_config("test", "gemini", config_path=config_file)
    adapter = GeminiAdapter(endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/models/gemini-image-test:generateContent")
        parts = body["contents"][0]["parts"]
        assert len([part for part in parts if "inlineData" in part]) == 2
        assert parts[-1] == {"text": "merge them"}
        assert body["generationConfig"]["imageConfig"] == {
            "aspectRatio": "16:9",
            "imageSize": "2K",
        }
        encoded = base64.b64encode(b"gemini").decode()
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": encoded,
                                        "mimeType": "image/png",
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(adapter, "_client", lambda: _mock_client(handler))
    result = adapter.execute(
        ImageRequest(
            operation="edit",
            prompt="merge them",
            model=endpoint.resolve_model(None, "edit"),
            references=refs,
            aspect_ratio="16:9",
            image_size="2K",
        )
    )
    assert result[0].data == b"gemini"


def test_seedream_edit_payload(config_file: Path, tmp_path: Path, monkeypatch) -> None:
    reference = tmp_path / "a.png"
    reference.write_bytes(b"seed")
    endpoint = get_endpoint_config("test", "seedream", config_path=config_file)
    adapter = SeedreamAdapter(endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v3/images/generations"
        assert body["model"] == "doubao-seedream-5-0-pro-test"
        assert body["prompt"] == "<point>500 500</point> replace"
        assert body["image"].startswith("data:image/png;base64,")
        assert body["seed"] == 7
        assert body["watermark"] is False
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(b"seedream").decode()}]}
        )

    monkeypatch.setattr(adapter, "_client", lambda: _mock_client(handler))
    result = adapter.execute(
        ImageRequest(
            operation="edit",
            prompt="<point>500 500</point> replace",
            model=endpoint.resolve_model(None, "edit"),
            references=[reference],
            seed=7,
            watermark=False,
        )
    )
    assert result[0].data == b"seedream"


def test_seedream_group_stream_payload(config_file: Path, monkeypatch) -> None:
    endpoint = get_endpoint_config("test", "seedream", config_path=config_file)
    adapter = SeedreamAdapter(endpoint)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["sequential_image_generation"] == "auto"
        assert body["sequential_image_generation_options"] == {"max_images": 3}
        assert body["stream"] is True
        events = "\n".join(
            [
                "data: "
                + json.dumps({"data": [{"b64_json": base64.b64encode(value).decode()}]})
                for value in (b"one", b"two", b"three")
            ]
            + ["data: [DONE]"]
        )
        return httpx.Response(200, text=events)

    monkeypatch.setattr(adapter, "_client", lambda: _mock_client(handler))
    policy = replace(
        endpoint.resolve_model(None, "generate"),
        capabilities=frozenset({"sequential", "stream"}),
        max_outputs=3,
    )
    result = adapter.execute(
        ImageRequest(
            operation="generate",
            prompt="a sequence",
            model=policy,
            n=3,
            sequential="auto",
            stream=True,
        )
    )
    assert [artifact.data for artifact in result] == [b"one", b"two", b"three"]
