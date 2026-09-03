import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from matting_cli.client import MattingClient
from matting_cli.config import MattingConfig
from matting_cli.errors import MattingError
from matting_cli.service import MattingService, _validate_alpha_png


def _png(mode="RGB") -> bytes:
    output = io.BytesIO()
    color = (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)
    Image.new(mode, (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def test_probe_and_remove_follow_async_contract(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/status":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "service": {"status": "ok", "version": "test"},
                        "queue": {"size": 0},
                    },
                },
            )
        if request.url.path == "/api/capabilities":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "methods": {"birefnet": {}},
                        "models": ["birefnet-hr-matting"],
                    },
                },
            )
        if request.url.path == "/api/matting/generate":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"task_id": "task-1", "status": "pending"},
                },
            )
        if request.url.path == "/api/matting/tasks/task-1":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "task_id": "task-1",
                        "status": "completed",
                        "progress": 100,
                    },
                },
            )
        if request.url.path == "/api/matting/download/task-1":
            return httpx.Response(
                200, content=_png("RGBA"), headers={"content-type": "image/png"}
            )
        return httpx.Response(404)

    config = MattingConfig(
        "http://matting.test",
        poll_interval=0.1,
        max_wait_seconds=2,
        source=tmp_path / "agent_config.toml",
    )
    client = MattingClient(config, transport=httpx.MockTransport(handler))
    service = MattingService(config, client=client)
    source = tmp_path / "source.png"
    source.write_bytes(_png("RGB"))
    output = tmp_path / "result.png"

    result = service.remove(source, output)

    assert result["backend"] == "matting-api"
    assert result["selection"]["method"] == "birefnet"
    assert output.is_file()
    assert calls == [
        "/api/status",
        "/api/capabilities",
        "/api/matting/generate",
        "/api/matting/tasks/task-1",
        "/api/matting/download/task-1",
    ]


def test_dry_run_does_not_submit_or_write(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"service": {"status": "ok"}})
        if request.url.path == "/api/capabilities":
            return httpx.Response(
                200,
                json={"methods": {"birefnet": {}}, "models": ["birefnet-hr-matting"]},
            )
        raise AssertionError(f"unexpected request: {request.url.path}")

    config = MattingConfig("http://matting.test")
    service = MattingService(
        config, client=MattingClient(config, transport=httpx.MockTransport(handler))
    )
    source = tmp_path / "source.png"
    source.write_bytes(_png("RGB"))
    output = tmp_path / "result.png"

    result = service.remove(source, output, dry_run=True)

    assert result["backend"] == "dry-run"
    assert not output.exists()


def test_output_validation_rejects_fully_opaque_or_empty_alpha() -> None:
    opaque = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(opaque, format="PNG")
    with pytest.raises(MattingError, match="没有任何透明像素"):
        _validate_alpha_png(opaque.getvalue())

    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(output, format="PNG")
    with pytest.raises(MattingError, match="完全透明"):
        _validate_alpha_png(output.getvalue())
