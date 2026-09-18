from pathlib import Path

import httpx
from upscale_cli.client import UpscaleClient
from upscale_cli.config import UpscaleConfig


def test_client_contract_and_stream_download(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "test"})
        if request.url.path == "/api/tasks/upload":
            body = request.read()
            assert b'name="media_type"' in body and b"image" in body
            assert b'name="file"' in body and b"pixels" in body
            return httpx.Response(202, json={"id": "abc", "status": "queued"})
        if request.url.path == "/api/tasks/abc":
            return httpx.Response(200, json={"id": "abc", "status": "completed"})
        if request.url.path == "/api/tasks/abc/download":
            return httpx.Response(200, content=b"result")
        raise AssertionError(request.url)

    source = tmp_path / "input.png"
    source.write_bytes(b"pixels")
    client = UpscaleClient(
        UpscaleConfig(base_url="http://api.test"),
        transport=httpx.MockTransport(handler),
    )
    assert client.health()["version"] == "test"
    assert (
        client.submit(
            source, media_type="image", model="m", mode="upscale", scale=2,
            target_width=None, target_height=None, fit="contain", start=0,
            duration=0, target_fps=None, interpolation_model=None
        )["id"]
        == "abc"
    )
    assert client.task("abc")["status"] == "completed"
    output = tmp_path / "result.bin"
    assert client.download_to("abc", output) == 6
    assert output.read_bytes() == b"result"
    assert client.download_url("abc") == "http://api.test/api/tasks/abc/download"
    assert len(requests) == 4


def test_video_submit_sends_mode_and_interpolation_fields(tmp_path: Path) -> None:
    captured: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.read())
        return httpx.Response(202, json={"id": "video-task", "status": "queued"})

    source = tmp_path / "input.mp4"
    source.write_bytes(b"video")
    client = UpscaleClient(
        UpscaleConfig(base_url="http://api.test"),
        transport=httpx.MockTransport(handler),
    )
    client.submit(
        source, media_type="video", model="m", mode="enhance", scale=None,
        target_width=None, target_height=1080, fit="contain", start=1,
        duration=20, target_fps="60", interpolation_model="rife-v4.25"
    )
    body = captured[0]
    for name, value in (
        (b"mode", b"enhance"),
        (b"target_height", b"1080"),
        (b"target_fps", b"60"),
        (b"interpolation_model", b"rife-v4.25"),
    ):
        assert b'name="' + name + b'"' in body
        assert value in body
