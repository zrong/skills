"""Optional bridge from imggen to the independently installed matting skill."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from imggen.chroma import remove_chroma_key
from imggen.models import ImggenError

SKILL_DIR = Path(__file__).resolve().parents[2]


def _matting_project() -> Path | None:
    candidates = []
    configured = os.environ.get("MATTING_SKILL_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            SKILL_DIR.parent / "matting",
            Path.home() / ".agents" / "skills" / "matting",
        ]
    )
    for candidate in candidates:
        project = candidate.resolve() / "scripts"
        if (project / "pyproject.toml").is_file():
            return project
    return None


def _command(project: Path, *args: str) -> list[str]:
    uv = shutil.which("uv")
    if not uv:
        raise FileNotFoundError("未找到 uv，无法运行 matting skill")
    return [uv, "run", "--project", str(project), "matting", *args]


def _run(
    command: list[str], *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


def _json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ImggenError("matting skill 返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise ImggenError("matting skill 返回了非对象 JSON")
    return value


def remove_background(
    input_path: str,
    output_path: str,
    *,
    config_path: str | None = None,
    method: str | None = None,
    model: str | None = None,
    parameters_json: str | None = None,
    reprocess: bool = False,
    use_matting: bool = True,
    fallback: bool = True,
    fallback_key_color: str = "#00ff00",
    fallback_auto_key: str = "border",
    fallback_tolerance: int = 12,
    fallback_transparent_threshold: float = 12.0,
    fallback_opaque_threshold: float = 96.0,
    fallback_edge_feather: float = 0.0,
    fallback_edge_contract: int = 0,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise ImggenError(f"输入图片不存在: {source}")
    if output.suffix.lower() != ".png":
        raise ImggenError("remove-background 输出必须使用 .png")
    if output.exists() and not force:
        raise ImggenError(f"输出已存在，拒绝覆盖: {output}")

    unavailable_reason = "matting 已由调用方禁用"
    project = _matting_project() if use_matting else None
    if use_matting and project is None:
        unavailable_reason = "未找到独立 matting skill"
    elif project is not None:
        probe_args = ["status", "--non-interactive"]
        if config_path:
            probe_args.extend(["--config", config_path])
        try:
            probe = _run(_command(project, *probe_args), timeout=45)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            probe = None
            unavailable_reason = str(exc)
        if probe is not None and probe.returncode == 0:
            command = [
                "remove",
                "--non-interactive",
                "--input",
                str(source),
                "--out",
                str(output),
            ]
            if config_path:
                command.extend(["--config", config_path])
            if method:
                command.extend(["--method", method])
            if model:
                command.extend(["--model", model])
            if parameters_json:
                command.extend(["--parameters-json", parameters_json])
            if reprocess:
                command.append("--reprocess")
            if force:
                command.append("--force")
            if dry_run:
                command.append("--dry-run")
            result = _run(_command(project, *command))
            if result.returncode != 0:
                message = result.stderr.strip() or f"exit {result.returncode}"
                raise ImggenError(
                    f"matting-api 已通过探测，但抠图执行失败，拒绝静默回退: {message}"
                )
            payload = _json_output(result)
            payload["integration"] = "image-generation"
            return payload
        if probe is not None:
            unavailable_reason = (
                probe.stderr.strip() or f"matting status exit {probe.returncode}"
            )

    if not fallback:
        raise ImggenError(f"matting 不可用且已禁用本地回退: {unavailable_reason}")
    if dry_run:
        return {
            "backend": "chroma-key-dry-run",
            "fallback_reason": unavailable_reason,
            "input": str(source),
            "output": str(output),
            "auto_key": fallback_auto_key,
            "key_color": fallback_key_color,
        }
    result = remove_chroma_key(
        str(source),
        str(output),
        key_color=fallback_key_color,
        tolerance=fallback_tolerance,
        auto_key=fallback_auto_key,
        soft_matte=True,
        transparent_threshold=fallback_transparent_threshold,
        opaque_threshold=fallback_opaque_threshold,
        edge_feather=fallback_edge_feather,
        edge_contract=fallback_edge_contract,
        spill_cleanup=True,
        force=force,
    )
    return {
        "backend": "chroma-key",
        "fallback_reason": unavailable_reason,
        **result,
    }
