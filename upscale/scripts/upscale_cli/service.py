from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

from .client import UpscaleClient
from .config import UpscaleConfig
from .errors import ApiError, ServiceUnavailableError, UpscaleError
from .filebrowser import FileBrowserGateway, normalize_remote_path, remote_output_path
from .naming import default_output_name

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class UpscaleService:
    def __init__(self, config: UpscaleConfig, *, client: UpscaleClient | None = None):
        self.config = config
        self.client = client or UpscaleClient(config)

    def probe(self) -> dict[str, Any]:
        health = self.client.health()
        status = self.client.status()
        capabilities = self.client.capabilities()
        if str(health.get("status") or "").lower() not in {
            "ok",
            "healthy",
            "ready",
            "running",
        }:
            raise ServiceUnavailableError(
                f"upscale-api health 不可用: {health.get('status') or '(empty)'}"
            )
        models = capabilities.get("models")
        defaults = capabilities.get("submission_defaults")
        submission = capabilities.get("task_submission")
        if not isinstance(models, dict) or not models:
            raise ServiceUnavailableError("upscale-api 未返回可用模型")
        if not isinstance(defaults, dict):
            raise ServiceUnavailableError("upscale-api 未返回 submission_defaults")
        if not isinstance(submission, dict) or not isinstance(
            submission.get("upload"), dict
        ):
            raise ServiceUnavailableError("upscale-api 未广告上传提交合同")
        if submission["upload"].get("url") != "/api/tasks/upload":
            raise ServiceUnavailableError("upscale-api 上传提交端点与 CLI 合同不兼容")
        return {
            "available": True,
            "config_path": str(self.config.source) if self.config.source else None,
            "base_url": self.config.base_url,
            "health": health,
            "status": status,
            "capabilities": capabilities,
        }

    def plan(
        self,
        input_path: str | Path,
        *,
        media_type: str | None = None,
        model: str | None = None,
        mode: str = "upscale",
        scale: float | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        fit: str = "contain",
        start: float = 0,
        duration: float = 0,
        target_fps: str | None = None,
        interpolation_model: str | None = None,
        output_path: str | Path | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        source = Path(input_path).expanduser().resolve()
        if not source.is_file():
            raise UpscaleError(f"输入文件不存在: {source}")
        size = source.stat().st_size
        if size <= 0:
            raise UpscaleError(f"输入文件为空: {source}")
        if self.config.max_input_bytes and size > self.config.max_input_bytes:
            raise UpscaleError(
                f"输入文件超过 max_input_bytes ({size} > {self.config.max_input_bytes})"
            )
        selected_media = infer_media_type(source, media_type)
        _validate_parameters(
            selected_media, mode=mode, scale=scale, target_width=target_width,
            target_height=target_height, fit=fit, start=start, duration=duration,
            target_fps=target_fps, interpolation_model=interpolation_model
        )
        live = self.probe()
        capabilities = live["capabilities"]
        selected_model = _select_model(capabilities, selected_media, model)
        inspection = inspect_input(source, selected_media, capabilities)
        selected_interpolation = _validate_input_fps(
            selected_media, inspection, target_fps, interpolation_model
        )
        output = Path(output_path).expanduser().resolve() if output_path else None
        if output:
            expected = ".png" if selected_media == "image" else ".mp4"
            if output.suffix.lower() != expected:
                raise UpscaleError(f"{selected_media} 输出必须使用 {expected}")
            if output.exists() and not force:
                raise UpscaleError(f"输出已存在，拒绝覆盖: {output}")
        return {
            "input": str(source),
            "input_bytes": size,
            "output": str(output) if output else None,
            "media_type": selected_media,
            "model": selected_model,
            "mode": mode if selected_media == "video" else "upscale",
            "scale": (2 if scale is None else scale)
            if selected_media == "image"
            else scale,
            "target_width": target_width if selected_media == "video" else None,
            "target_height": target_height if selected_media == "video" else None,
            "fit": fit if selected_media == "video" else None,
            "start": start if selected_media == "video" else None,
            "duration": duration if selected_media == "video" else None,
            "target_fps": target_fps if selected_media == "video" else None,
            "interpolation_model": selected_interpolation,
            "inspection": inspection,
            "live": live,
        }

    def run(
        self,
        input_path: str | Path,
        *,
        output_path: str | Path | None = None,
        media_type: str | None = None,
        model: str | None = None,
        mode: str = "upscale",
        scale: float | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        fit: str = "contain",
        start: float = 0,
        duration: float = 0,
        target_fps: str | None = None,
        interpolation_model: str | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(
            input_path,
            media_type=media_type,
            model=model,
            mode=mode,
            scale=scale,
            target_width=target_width,
            target_height=target_height,
            fit=fit,
            start=start,
            duration=duration,
            target_fps=target_fps,
            interpolation_model=interpolation_model,
            output_path=output_path,
            force=force,
        )
        if dry_run:
            return {"dry_run": True, **plan}
        submitted = self.client.submit(
            Path(plan["input"]),
            media_type=plan["media_type"],
            model=plan["model"],
            mode=plan["mode"],
            scale=plan["scale"],
            target_width=plan["target_width"],
            target_height=plan["target_height"],
            fit=str(plan["fit"] or "contain"),
            start=float(plan["start"] or 0),
            duration=float(plan["duration"] or 0),
            target_fps=plan["target_fps"],
            interpolation_model=plan["interpolation_model"],
        )
        task_id = str(submitted.get("id") or "").strip()
        if not task_id:
            raise ApiError("upscale-api 提交成功响应缺少任务 id")
        terminal = self._wait(task_id)
        result: dict[str, Any] = {
            "backend": "upscale-api",
            "task_id": task_id,
            "download_url": self.client.download_url(task_id),
            "task": terminal,
            **{key: value for key, value in plan.items() if key != "live"},
            "live": plan["live"],
        }
        if output_path:
            output = Path(str(plan["output"]))
        else:
            output = Path(plan["input"]).with_name(
                _default_name_from_task(
                    Path(plan["input"]).name, plan["media_type"], terminal,
                    fallback_model=plan["model"]
                )
            )
        result.update(
            self._download_verified(
                task_id, output, plan["media_type"], force=force
            )
        )
        return result

    def _wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.max_wait_seconds
        while time.monotonic() < deadline:
            current = self.client.task(task_id)
            state = str(current.get("status") or "").lower()
            if state == "completed":
                return current
            if state in {"failed", "cancelled"}:
                raise ApiError(str(current.get("error") or f"upscale-api 任务{state}"))
            if state not in {"queued", "running", "cancelling"}:
                raise ApiError(f"upscale-api 返回未知任务状态: {state or '(empty)'}")
            time.sleep(self.config.poll_interval)
        raise ApiError(f"upscale-api 任务超时: {task_id}")

    def _download_verified(
        self, task_id: str, output: Path, media_type: str, *, force: bool
    ) -> dict[str, Any]:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not force:
            raise UpscaleError(f"输出已存在，拒绝覆盖: {output}")
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.", suffix=".part", dir=output.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            size = self.client.download_to(task_id, temporary)
            validation = validate_result(temporary, media_type)
            if force:
                os.replace(temporary, output)
            else:
                try:
                    os.link(temporary, output)
                except FileExistsError as exc:
                    raise UpscaleError(f"输出已存在，拒绝覆盖: {output}") from exc
                temporary.unlink()
            return {
                "output": str(output),
                "output_bytes": size,
                "validation": validation,
            }
        finally:
            temporary.unlink(missing_ok=True)


def run_filebrowser(
    service: UpscaleService,
    remote_input: str,
    *,
    source_name: str | None = None,
    remote_output: str | None = None,
    local_output: str | Path | None = None,
    media_type: str | None = None,
    model: str | None = None,
    mode: str = "upscale",
    scale: float | None = None,
    target_width: int | None = None,
    target_height: int | None = None,
    fit: str = "contain",
    start: float = 0,
    duration: float = 0,
    target_fps: str | None = None,
    interpolation_model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    gateway: FileBrowserGateway | None = None,
) -> dict[str, Any]:
    remote = normalize_remote_path(remote_input)
    selected_media = infer_media_type(PurePosixPath(remote), media_type)
    destination = normalize_remote_path(remote_output) if remote_output else None
    expected_suffix = ".png" if selected_media == "image" else ".mp4"
    if destination and PurePosixPath(destination).suffix.lower() != expected_suffix:
        raise UpscaleError(
            f"FileBrowser {selected_media} 输出必须使用 {expected_suffix}"
        )
    fb = gateway or FileBrowserGateway(service.config)
    local_copy = Path(local_output).expanduser().resolve() if local_output else None
    if local_copy and local_copy.exists() and not force:
        raise UpscaleError(f"本地输出已存在，拒绝覆盖: {local_copy}")

    if dry_run:
        suffix = PurePosixPath(remote).suffix
        placeholder = Path(tempfile.gettempdir()) / f"upscale-dry-run-input{suffix}"
        fb.get(remote, placeholder, source=source_name, dry_run=True)
        live = service.probe()
        selected_model = _select_model(live["capabilities"], selected_media, model)
        _validate_parameters(
            selected_media, mode=mode, scale=scale, target_width=target_width,
            target_height=target_height, fit=fit, start=start, duration=duration,
            target_fps=target_fps, interpolation_model=interpolation_model
        )
        return {
            "dry_run": True,
            "filebrowser_source": source_name,
            "filebrowser_input": remote,
            "filebrowser_output": destination,
            "default_output_pattern": (
                "输入文件名_WxH_模型名称.png" if selected_media == "image"
                else "输入文件名_短边p_实际fps_模型名称.mp4"
            ) if destination is None else None,
            "local_output": str(local_copy) if local_copy else None,
            "media_type": selected_media,
            "model": selected_model,
            "mode": mode if selected_media == "video" else "upscale",
            "scale": (2 if scale is None else scale)
            if selected_media == "image"
            else scale,
            "target_width": target_width if selected_media == "video" else None,
            "target_height": target_height if selected_media == "video" else None,
            "fit": fit if selected_media == "video" else None,
            "start": start if selected_media == "video" else None,
            "duration": duration if selected_media == "video" else None,
            "target_fps": target_fps if selected_media == "video" else None,
            "interpolation_model": (
                (interpolation_model or "rife-v4.25")
                if selected_media == "video" and target_fps is not None
                else None
            ),
            "live": live,
        }

    with tempfile.TemporaryDirectory(prefix="upscale-filebrowser-") as work:
        workdir = Path(work)
        local_input = workdir / PurePosixPath(remote).name
        api_output = workdir / f"result{expected_suffix}"
        fetched = fb.get(remote, local_input, source=source_name)
        result = service.run(
            local_input,
            output_path=api_output,
            media_type=selected_media,
            model=model,
            mode=mode,
            scale=scale,
            target_width=target_width,
            target_height=target_height,
            fit=fit,
            start=start,
            duration=duration,
            target_fps=target_fps,
            interpolation_model=interpolation_model,
        )
        if destination is None:
            destination = remote_output_path(
                remote,
                selected_media,
                result["model"],
                width=result["validation"].get("width"),
                height=result["validation"].get("height"),
                fps=result["validation"].get("avg_frame_rate"),
            )
        uploaded = fb.put(api_output, destination, source=source_name, overwrite=force)
        if local_copy:
            local_copy.parent.mkdir(parents=True, exist_ok=True)
            if local_copy.exists() and force:
                local_copy.unlink()
            shutil.copy2(api_output, local_copy)
        return {
            **result,
            "input": remote,
            "output": str(local_copy) if local_copy else None,
            "filebrowser_source": (
                uploaded.get("source_name") or fetched.get("source_name") or source_name
            ),
            "filebrowser_input": remote,
            "filebrowser_output": destination,
            "filebrowser_bytes": uploaded.get("size") or result.get("output_bytes"),
            "local_output": str(local_copy) if local_copy else None,
        }


def _default_name_from_task(
    input_name: str, media_type: str, task: dict[str, Any], *, fallback_model: str
) -> str:
    metrics = task.get("metrics") if isinstance(task.get("metrics"), dict) else {}
    return default_output_name(
        input_name,
        media_type,
        str(task.get("model") or fallback_model),
        width=metrics.get("width"),
        height=metrics.get("height"),
        fps=metrics.get("fps"),
    )


def infer_media_type(path: Path | PurePosixPath, explicit: str | None = None) -> str:
    suffix = path.suffix.lower()
    inferred = (
        "image"
        if suffix in IMAGE_SUFFIXES
        else "video"
        if suffix in VIDEO_SUFFIXES
        else None
    )
    if explicit:
        if explicit not in {"image", "video"}:
            raise UpscaleError(f"未知 media_type: {explicit}")
        if inferred and inferred != explicit:
            raise UpscaleError(f"扩展名 {suffix} 与 --media-type {explicit} 冲突")
        return explicit
    if not inferred:
        raise UpscaleError(f"无法从扩展名判断图片或视频: {path}")
    return inferred


def _validate_parameters(
    media_type: str, *, mode: str, scale: float | None, target_width: int | None,
    target_height: int | None, fit: str, start: float, duration: float,
    target_fps: str | None, interpolation_model: str | None
) -> None:
    if media_type == "image":
        if mode != "upscale":
            raise UpscaleError("图片只支持 --mode upscale")
        if start or duration:
            raise UpscaleError("图片不接受 --start/--duration")
        if target_fps is not None or interpolation_model is not None:
            raise UpscaleError("图片不接受补帧参数")
        if target_width is not None or target_height is not None or fit != "contain":
            raise UpscaleError("图片不接受 --target-width/--target-height/--fit")
        actual_scale = 2 if scale is None else scale
        if not 1 < actual_scale <= 4:
            raise UpscaleError("图片 --scale 必须大于 1 且不超过 4")
    else:
        if mode not in {"upscale", "enhance", "resize"}:
            raise UpscaleError("视频 --mode 必须是 upscale、enhance 或 resize")
        if mode == "upscale" and scale is not None and scale not in {2, 4}:
            raise UpscaleError("upscale 模式的 --scale 仅支持 2 或 4")
        if mode in {"enhance", "resize"} and scale is not None and not 0.25 <= scale <= 4:
            raise UpscaleError(f"{mode} 模式的 --scale 必须在 0.25 到 4 之间")
        if scale is not None and (target_width is not None or target_height is not None):
            raise UpscaleError("视频 --scale 与目标尺寸不能同时使用")
        if mode == "resize" and scale is None and target_width is None and target_height is None:
            raise UpscaleError("resize 模式必须提供 --scale 或目标尺寸")
        for name, value in (("--target-width", target_width), ("--target-height", target_height)):
            if value is not None and (value < 64 or value > 3840 or value % 2):
                raise UpscaleError(f"视频 {name} 必须是64到3840之间的偶数")
        if fit not in {"contain", "cover"}:
            raise UpscaleError("视频 --fit 必须是 contain 或 cover")
        if fit == "cover" and (target_width is None or target_height is None):
            raise UpscaleError("视频 --fit cover 必须同时提供目标宽高")
        if start < 0 or duration < 0:
            raise UpscaleError("视频 --start/--duration 不能小于 0")
        if interpolation_model is not None and target_fps is None:
            raise UpscaleError("--interpolation-model 必须与 --target-fps 一起使用")
        if interpolation_model not in {None, "rife-v4.25", "rife-v4.25-lite"}:
            raise UpscaleError("未知补帧模型")
        if target_fps is not None:
            try:
                parsed = Fraction(target_fps)
            except (ValueError, ZeroDivisionError) as exc:
                raise UpscaleError("--target-fps 必须是数字或有理数，例如 60 或 60000/1001") from exc
            if parsed <= 0 or parsed > 120:
                raise UpscaleError("--target-fps 必须大于 0 且不超过 120")


def _validate_input_fps(
    media_type: str,
    inspection: dict[str, Any],
    target_fps: str | None,
    interpolation_model: str | None,
) -> str | None:
    if media_type != "video" or target_fps is None:
        return None
    try:
        source_fps = Fraction(str(inspection["avg_frame_rate"]))
        output_fps = Fraction(target_fps)
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise UpscaleError("无法校验输入视频帧率") from exc
    if output_fps <= source_fps:
        raise UpscaleError("--target-fps 必须高于输入视频帧率")
    if output_fps / source_fps > 4:
        raise UpscaleError("补帧倍率不能超过 4")
    return interpolation_model or "rife-v4.25"


def _select_model(
    capabilities: dict[str, Any], media_type: str, requested: str | None
) -> str:
    models = capabilities.get("models")
    defaults = capabilities.get("submission_defaults")
    if not isinstance(models, dict) or not isinstance(defaults, dict):
        raise ServiceUnavailableError("upscale-api 模型能力不完整")
    selected = requested or "realesrgan-x2plus"
    if requested and requested not in models:
        raise UpscaleError(f"upscale-api 未广告模型: {requested}")
    if selected not in models:
        raise UpscaleError(f"upscale-api 未广告标准模型: {selected}")
    details = models[selected]
    if isinstance(details, dict):
        supported = details.get("media_types")
        if isinstance(supported, list) and media_type not in supported:
            raise UpscaleError(f"模型 {selected} 不支持 {media_type}")
    return selected


def inspect_input(
    path: Path, media_type: str, capabilities: dict[str, Any]
) -> dict[str, Any]:
    if media_type == "image":
        try:
            with Image.open(path) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise UpscaleError("upscale-api 不支持动图输入")
                image.load()
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise UpscaleError(f"upscale-api 不支持图片格式: {image.format}")
                pixels = image.width * image.height
                image_capability = capabilities.get("image")
                maximum = (
                    image_capability.get("max_input_pixels")
                    if isinstance(image_capability, dict)
                    else None
                )
                if isinstance(maximum, int) and pixels > maximum:
                    raise UpscaleError(
                        f"输入图片超过服务 max_input_pixels ({pixels} > {maximum})"
                    )
                return {
                    "format": image.format,
                    "width": image.width,
                    "height": image.height,
                    "pixels": pixels,
                    "has_alpha": "A" in image.getbands(),
                }
        except UpscaleError:
            raise
        except Exception as exc:
            raise UpscaleError(f"输入图片无法解码: {exc}") from exc
    process = _run_ffprobe(
        path,
        "format=duration:stream=codec_name,width,height,avg_frame_rate",
        "输入视频",
    )
    if process.returncode != 0:
        raise UpscaleError(
            f"输入视频无法通过 ffprobe 校验: {(process.stderr or '').strip()[-500:]}"
        )
    try:
        payload = json.loads(process.stdout)
        stream = payload["streams"][0]
        return {
            "format": path.suffix.lower().lstrip("."),
            "codec": stream.get("codec_name"),
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "avg_frame_rate": stream.get("avg_frame_rate"),
            "duration": float(payload["format"]["duration"]),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpscaleError("输入视频缺少有效视频流") from exc


def validate_result(path: Path, media_type: str) -> dict[str, Any]:
    if media_type == "image":
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != "PNG":
                    raise UpscaleError(f"upscale-api 图片结果不是 PNG: {image.format}")
                return {"format": "PNG", "width": image.width, "height": image.height}
        except UpscaleError:
            raise
        except Exception as exc:
            raise UpscaleError(f"upscale-api 图片结果无法解码: {exc}") from exc
    process = _run_ffprobe(
        path,
        "stream=codec_name,width,height,avg_frame_rate",
        "upscale-api 视频结果",
    )
    if process.returncode != 0:
        raise UpscaleError(
            "upscale-api 视频结果无法通过 ffprobe 校验: "
            f"{(process.stderr or '').strip()[-500:]}"
        )
    try:
        payload = json.loads(process.stdout)
        stream = payload["streams"][0]
        return {
            "format": "MP4",
            "codec": stream.get("codec_name"),
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "avg_frame_rate": stream.get("avg_frame_rate"),
        }
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpscaleError("upscale-api 视频结果缺少有效视频流") from exc


def _run_ffprobe(
    path: Path, entries: str, label: str
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                entries,
                "-of",
                "json",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise UpscaleError(f"无法执行 ffprobe 校验{label}: {exc}") from exc
