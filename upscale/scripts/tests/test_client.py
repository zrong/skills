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
            source, media_type="image", model="m", scale=2, target_width=None,
            target_height=None, fit="contain", start=0, duration=0
        )["id"]
        == "abc"
    )
    assert client.task("abc")["status"] == "completed"
    output = tmp_path / "result.bin"
    assert client.download_to("abc", output) == 6
    assert output.read_bytes() == b"result"
    assert client.download_url("abc") == "http://api.test/api/tasks/abc/download"
    assert len(requests) == 4
