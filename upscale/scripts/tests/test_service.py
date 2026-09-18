from pathlib import Path

import pytest
from PIL import Image
from upscale_cli.config import UpscaleConfig
from upscale_cli.errors import UpscaleError
from upscale_cli.filebrowser import remote_output_path
from upscale_cli.service import (
    _validate_parameters,
    UpscaleService,
    infer_media_type,
    run_filebrowser,
    validate_result,
)


class FakeClient:
    def health(self):
        return {"status": "ok", "version": "1.2.3"}

    def status(self):
        return {"gpu": {"permitted": True}, "jobs": []}

    def capabilities(self):
        return {
            "models": {
                "image-model": {"media_types": ["image"]},
                "video-model": {"media_types": ["video"]},
                "realesrgan-x2plus": {"media_types": ["image", "video"]},
            },
            "submission_defaults": {"image": "image-model", "video": "video-model"},
            "task_submission": {"upload": {"url": "/api/tasks/upload"}},
        }

    def submit(self, *_args, **kwargs):
        assert kwargs["model"] == "realesrgan-x2plus"
        return {"id": "task-1", "status": "queued"}

    def task(self, _task_id):
        return {
            "id": "task-1",
            "status": "completed",
            "model": "realesrgan-x2plus",
            "metrics": {"width": 8, "height": 6},
        }

    def download_url(self, task_id):
        return f"http://api.test/api/tasks/{task_id}/download"

    def download_to(self, _task_id, destination):
        Image.new("RGB", (8, 6), "red").save(destination, format="PNG")
        return destination.stat().st_size


def test_local_image_run_downloads_and_validates(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 3), "blue").save(source)
    output = tmp_path / "output.png"
    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test"), client=FakeClient()
    )
    result = service.run(source, output_path=output, scale=2)
    assert result["task_id"] == "task-1"
    assert result["download_url"].endswith("/task-1/download")
    assert result["validation"]["width"] == 8
    assert output.is_file()


def test_default_image_scale_is_explicit_in_plan(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 3), "blue").save(source)
    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test"), client=FakeClient()
    )
    assert service.plan(source)["scale"] == 2


def test_run_without_output_uses_default_output_filename(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 3), "blue").save(source)
    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test"), client=FakeClient()
    )
    result = service.run(source)
    assert result["output"] == str(
        tmp_path / "input_8x6_realesrgan-x2plus.png"
    )
    assert Path(result["output"]).is_file()
    assert result["output_bytes"] > 0
    assert result["validation"] == {"format": "PNG", "width": 8, "height": 6}


def test_output_overwrite_and_media_parameter_guards(tmp_path: Path) -> None:
    source = tmp_path / "input.png"
    Image.new("RGB", (4, 3)).save(source)
    output = tmp_path / "output.png"
    output.write_bytes(b"existing")
    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test"), client=FakeClient()
    )
    with pytest.raises(UpscaleError, match="拒绝覆盖"):
        service.run(source, output_path=output)
    with pytest.raises(UpscaleError, match="不接受 --start"):
        service.run(source, start=1)


def test_media_and_remote_output_rules() -> None:
    assert infer_media_type(Path("a.webp")) == "image"
    assert infer_media_type(Path("a.mov")) == "video"
    assert remote_output_path(
        "/dir/photo.jpg", "image", "realesrgan-x2plus", width=2048, height=1536
    ) == "/dir/photo_2048x1536_realesrgan-x2plus.png"
    assert remote_output_path(
        "/dir/clip.mkv", "video", "realcugan-pro-x2", width=1920,
        height=1080, fps="60000/1001"
    ) == "/dir/clip_1080p_59.94fps_realcugan-pro-x2.mp4"


def test_video_size_parameter_guards() -> None:
    common = {"start": 0, "duration": 0, "target_fps": None,
              "interpolation_model": None}
    _validate_parameters("video", mode="upscale", scale=2, target_width=None,
                         target_height=None, fit="contain", **common)
    _validate_parameters("video", mode="upscale", scale=None, target_width=1920,
                         target_height=1080, fit="cover", **common)
    _validate_parameters("video", mode="enhance", scale=0.5, target_width=None,
                         target_height=None, fit="contain", **common)
    with pytest.raises(UpscaleError, match="仅支持 2 或 4"):
        _validate_parameters("video", mode="upscale", scale=3, target_width=None,
                             target_height=None, fit="contain", **common)
    with pytest.raises(UpscaleError, match="不能同时"):
        _validate_parameters("video", mode="upscale", scale=2, target_width=None,
                             target_height=1080, fit="contain", **common)
    with pytest.raises(UpscaleError, match="同时提供目标宽高"):
        _validate_parameters("video", mode="upscale", scale=None, target_width=None,
                             target_height=1080, fit="cover", **common)
    with pytest.raises(UpscaleError, match="必须提供"):
        _validate_parameters("video", mode="resize", scale=None, target_width=None,
                             target_height=None, fit="contain", **common)


def test_interpolation_parameter_guards() -> None:
    base = {"media_type": "video", "mode": "upscale", "scale": 2,
            "target_width": None, "target_height": None, "fit": "contain",
            "start": 0, "duration": 0}
    _validate_parameters(**base, target_fps="60000/1001",
                         interpolation_model="rife-v4.25")
    with pytest.raises(UpscaleError, match="必须与 --target-fps"):
        _validate_parameters(**base, target_fps=None,
                             interpolation_model="rife-v4.25-lite")
    with pytest.raises(UpscaleError, match="不超过 120"):
        _validate_parameters(**base, target_fps="121", interpolation_model=None)


def test_cancel_reads_state_then_waits_for_cancelled_terminal() -> None:
    class CancellingClient(FakeClient):
        def __init__(self) -> None:
            self.task_reads = 0

        def task(self, _task_id):
            self.task_reads += 1
            return {
                "id": "task-1",
                "status": "queued" if self.task_reads == 1 else "cancelled",
            }

        def cancel(self, task_id):
            assert task_id == "task-1"
            return {"id": task_id, "status": "cancelling"}

    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test", poll_interval=0.1),
        client=CancellingClient(),
    )
    result = service.cancel("task-1", wait=True)
    assert result["cancellation_requested"] is True
    assert result["before"]["status"] == "queued"
    assert result["task"]["status"] == "cancelled"


def test_image_validation_rejects_non_png(tmp_path: Path) -> None:
    path = tmp_path / "bad.png"
    Image.new("RGB", (2, 2)).save(path, format="JPEG")
    with pytest.raises(UpscaleError, match="不是 PNG"):
        validate_result(path, "image")


class FakeFileBrowserGateway:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str, bool]] = []

    def get(self, remote, local, *, source=None, dry_run=False):
        assert remote == "/project/photo.jpg"
        assert source == "production"
        assert dry_run is False
        Image.new("RGB", (4, 3), "blue").save(local, format="JPEG")
        return {
            "source_name": source,
            "remote_path": remote,
            "size": local.stat().st_size,
        }

    def put(self, local, remote, *, source=None, overwrite=False):
        assert local.is_file()
        self.put_calls.append((remote, source, overwrite))
        return {
            "source_name": source,
            "remote_path": remote,
            "size": local.stat().st_size,
        }


def test_filebrowser_flow_returns_result_to_same_directory() -> None:
    service = UpscaleService(
        UpscaleConfig(base_url="http://api.test"), client=FakeClient()
    )
    gateway = FakeFileBrowserGateway()
    result = run_filebrowser(
        service,
        "/project/photo.jpg",
        source_name="production",
        gateway=gateway,
    )
    assert result["filebrowser_output"] == (
        "/project/photo_8x6_realesrgan-x2plus.png"
    )
    assert result["filebrowser_source"] == "production"
    assert result["download_url"].endswith("/task-1/download")
    assert gateway.put_calls == [
        ("/project/photo_8x6_realesrgan-x2plus.png", "production", False)
    ]
