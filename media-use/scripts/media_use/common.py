"""media_use 公共模块：ffmpeg / ffprobe 封装与共享工具。"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()

# 时间格式：HH:MM:SS[.ms] / MM:SS[.ms] / 纯秒数
_TIME_PATTERN = re.compile(
    r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.\d{1,3})?$"  # HH:MM:SS 或 MM:SS
    r"|^\d+(?:\.\d+)?$"                                  # 纯秒数
)


@dataclass
class MediaInfo:
    """ffprobe 探测结果。"""

    path: Path
    width: int | None
    height: int | None
    codec_name: str | None      # 视频编码
    profile: str | None
    pix_fmt: str | None
    r_frame_rate: str | None
    bit_rate: str | None
    audio_codec: str | None

    def digest(self) -> str:
        """用于一致性比较的指纹（codec / profile / 分辨率 / pix_fmt）。"""
        return f"{self.codec_name}|{self.profile}|{self.width}x{self.height}|{self.pix_fmt}"


def ensure_ffmpeg() -> None:
    """检查 ffmpeg / ffprobe 可用，缺失则报错退出。"""
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        console.print(f"[red]错误：未找到 {' / '.join(missing)}。请先安装 ffmpeg。[/red]")
        raise SystemExit(1)


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe(path: Path) -> MediaInfo | None:
    """用 ffprobe 探测媒体信息；失败返回 None。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,bit_rate,codec_type",
        "-of", "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None

    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return MediaInfo(
        path=Path(path),
        width=_to_int(v.get("width")),
        height=_to_int(v.get("height")),
        codec_name=v.get("codec_name"),
        profile=v.get("profile"),
        pix_fmt=v.get("pix_fmt"),
        r_frame_rate=v.get("r_frame_rate"),
        bit_rate=v.get("bit_rate"),
        audio_codec=a.get("codec_name"),
    )


def run_ffmpeg(args: list[str], *, dry_run: bool = False) -> bool:
    """执行 ffmpeg（args 不含开头的 ffmpeg）；dry_run 时仅打印命令。返回是否成功。"""
    if dry_run:
        console.print(f"[yellow][dry-run][/yellow] {shlex.join(['ffmpeg', *args])}")
        return True
    try:
        # -y：自动覆盖已存在的输出。ffmpeg 8.x 在非交互 stdin 下遇到「文件已存在」
        # 会回复 N 并以退出码 0 退出（不写入但看似成功），故必须显式 -y 才能可靠覆盖。
        result = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    except FileNotFoundError:
        ensure_ffmpeg()
        return False
    if result.returncode != 0:
        console.print(f"[red]ffmpeg 失败（返回码 {result.returncode}）[/red]")
        err = (result.stderr or "").strip()
        if err:
            console.print(f"[dim]{err[-800:]}[/dim]")
        return False
    # 防御：ffmpeg 个别情况下返回 0 但实际未写出（如输出已存在且未 -y）。
    if "Not overwriting" in (result.stderr or "") or "Error opening output file" in (
        result.stderr or ""
    ):
        console.print("[red]ffmpeg 未写出输出文件（可能未覆盖已存在文件）[/red]")
        return False
    return True


def parse_time(s: str) -> str:
    """校验时间字符串（HH:MM:SS.ms / MM:SS / 秒数），原样返回。"""
    s = s.strip()
    if not _TIME_PATTERN.match(s):
        raise ValueError(
            f"无效的时间格式：'{s}'。支持 HH:MM:SS.ms、MM:SS 或秒数（如 90 或 1:30）"
        )
    return s


def time_to_seconds(s: str) -> float:
    """把时间字符串（HH:MM:SS.ms / MM:SS / 秒数）换算为秒数。"""
    s = s.strip()
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    ms = 0.0
    if "." in parts[-1]:
        main, frac = parts[-1].split(".", 1)
        parts[-1] = main
        ms = float(f"0.{frac}")
    hours = int(parts[0]) if len(parts) == 3 else 0
    minutes = int(parts[-2])
    seconds = int(parts[-1])
    return float(hours * 3600 + minutes * 60 + seconds) + ms


def prepare_target_dir(target: Path) -> None:
    """目标目录：不存在则创建；存在且非空则报错退出。"""
    if target.exists():
        if any(target.iterdir()):
            console.print(f"[red]错误：目标文件夹不为空：{target}[/red]")
            console.print("请清空目标文件夹或指定一个新的文件夹")
            raise SystemExit(1)
    else:
        target.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]已创建目标文件夹：{target}[/green]")
