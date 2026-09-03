"""High-level probe, planning, execution, and output validation."""

from __future__ import annotations

import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .client import MattingClient
from .config import MattingConfig
from .errors import ApiError, MattingError, ServiceUnavailableError
from .inspection import inspect_image
from .selection import advertised, available_pairs, select


class MattingService:
    def __init__(self, config: MattingConfig, *, client: MattingClient | None = None):
        self.config = config
        self.client = client or MattingClient(config)

    def probe(self) -> dict[str, Any]:
        status = self.client.status()
        capabilities = self.client.capabilities()
        service = (
            status.get("service") if isinstance(status.get("service"), dict) else {}
        )
        state = str(service.get("status") or status.get("status") or "").lower()
        if state and state not in {"ok", "running", "ready", "healthy"}:
            raise ServiceUnavailableError(f"matting-api 状态不可用: {state}")
        methods, models = advertised(capabilities)
        if not methods or not models:
            raise ServiceUnavailableError("matting-api 未返回可用算法或模型")
        pairs = available_pairs(self.config, capabilities)
        if not pairs:
            raise ServiceUnavailableError(
                "matting-api 没有可证明兼容的算法/模型组合；请更新服务能力或 [matting.models]"
            )
        return {
            "available": True,
            "config_path": str(self.config.source) if self.config.source else None,
            "base_url": self.config.base_url,
            "status": status,
            "capabilities": capabilities,
            "compatible_pairs": pairs,
        }

    def plan(
        self,
        input_path: str | Path,
        *,
        method: str | None = None,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
        reprocess: bool = False,
    ) -> dict[str, Any]:
        live = self.probe()
        inspection = inspect_image(
            input_path,
            max_input_bytes=self.config.max_input_bytes,
            max_pixels=self.config.max_pixels,
        )
        selection = select(
            inspection,
            self.config,
            live["capabilities"],
            method=method,
            model=model,
            parameters=parameters,
            reprocess=reprocess,
        )
        return {
            "live": live,
            "inspection": inspection,
            "selection": selection.to_dict(),
        }

    def remove(
        self,
        input_path: str | Path,
        output_path: str | Path,
        *,
        method: str | None = None,
        model: str | None = None,
        parameters: dict[str, Any] | None = None,
        reprocess: bool = False,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        source = Path(input_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".png":
            raise MattingError("matting 输出必须使用 .png")
        if output.exists() and not force:
            raise MattingError(f"输出已存在，拒绝覆盖: {output}")
        plan = self.plan(
            source,
            method=method,
            model=model,
            parameters=parameters,
            reprocess=reprocess,
        )
        if dry_run:
            return {
                "backend": "dry-run",
                "planned_backend": (
                    "existing-alpha"
                    if plan["selection"]["action"] == "preserve_existing_alpha"
                    else "matting-api"
                ),
                "output": str(output),
                **plan,
            }

        selection = plan["selection"]
        if selection["action"] == "preserve_existing_alpha":
            content = _normalize_existing_alpha(source)
            _write_atomic(output, content, force=force)
            return {"backend": "existing-alpha", "output": str(output), **plan}

        submitted = self.client.submit(
            source,
            method=str(selection["method"]),
            model=str(selection["model"]),
            parameters=dict(selection["parameters"]),
        )
        task_id = str(submitted.get("task_id") or "").strip()
        if not task_id:
            raise ApiError("matting-api 提交成功响应缺少 task_id")
        deadline = time.monotonic() + self.config.max_wait_seconds
        terminal: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            current = self.client.task(task_id)
            state = str(current.get("status") or "").lower()
            if state == "completed":
                terminal = current
                break
            if state == "failed":
                raise ApiError(str(current.get("error") or "matting-api 任务失败"))
            if state not in {"pending", "processing"}:
                raise ApiError(f"matting-api 返回未知任务状态: {state or '(empty)'}")
            time.sleep(self.config.poll_interval)
        if terminal is None:
            raise ApiError(f"matting-api 任务超时: {task_id}")
        content = self.client.download(task_id)
        _validate_alpha_png(content)
        _write_atomic(output, content, force=force)
        return {
            "backend": "matting-api",
            "task_id": task_id,
            "task": terminal,
            "output": str(output),
            **plan,
        }


def _normalize_existing_alpha(source: Path) -> bytes:
    with Image.open(source) as image:
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        content = output.getvalue()
    _validate_alpha_png(content)
    return content


def _validate_alpha_png(content: bytes) -> None:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            if image.format != "PNG":
                raise MattingError(f"matting-api 下载结果不是 PNG: {image.format}")
            if "A" not in image.getbands():
                raise MattingError("matting-api 下载结果缺少 alpha 通道")
            alpha_min, alpha_max = image.getchannel("A").getextrema()
            if alpha_min == 255:
                raise MattingError("matting-api 下载结果没有任何透明像素")
            if alpha_max == 0:
                raise MattingError("matting-api 下载结果完全透明，未保留主体")
    except MattingError:
        raise
    except Exception as exc:
        raise MattingError(f"matting-api 下载结果无法解码: {exc}") from exc


def _write_atomic(output: Path, content: bytes, *, force: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise MattingError(f"输出已存在，拒绝覆盖: {output}")
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary, output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError as exc:
                raise MattingError(f"输出已存在，拒绝覆盖: {output}") from exc
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()
