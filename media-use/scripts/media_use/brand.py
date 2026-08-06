#!/usr/bin/env python3
"""ffmpeg_brand: 添加图片水印、拼接片尾并按目标体积转码。"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import typer

from .common import console, ensure_ffmpeg, run_ffmpeg

app = typer.Typer(help="添加图片水印、片尾并压缩视频")

_BITRATE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)([kKmM]?)$")
_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}
_SCOPES = {"main", "all"}
_PRESETS = {
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
}
_TARGET_RESERVE = 0.95
_DEFAULT_WATERMARK_SHORT_EDGE_RATIO = 0.30
_BUNDLED_CJK_FONT = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "fonts"
    / "SourceHanSansSC-Regular.otf"
)


@dataclass(frozen=True)
class BrandMediaInfo:
    """生成品牌视频所需的媒体信息。"""

    duration: float
    width: int
    height: int
    has_audio: bool


def probe_brand_media(path: Path) -> BrandMediaInfo:
    """读取视频时长、尺寸和音轨状态。"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as exc:
        raise ValueError(f"无法读取媒体信息：{path}") from exc

    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"文件不包含视频轨：{path}")

    duration_value = video.get("duration") or data.get("format", {}).get("duration")
    try:
        duration = float(duration_value)
        width = int(video["width"])
        height = int(video["height"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"媒体时长或尺寸无效：{path}") from exc
    if duration <= 0 or width <= 0 or height <= 0:
        raise ValueError(f"媒体时长或尺寸无效：{path}")

    return BrandMediaInfo(
        duration=duration,
        width=width,
        height=height,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
    )


def parse_bitrate_kbps(value: str) -> float:
    """将 64k、1.5M 或 128 解析为 kbps。"""
    match = _BITRATE_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无效码率：{value}。示例：64k、1500k、1.5M")
    amount = float(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "m":
        amount *= 1000
    if amount <= 0:
        raise ValueError("码率必须大于 0")
    return amount


def calculate_video_bitrate_kbps(
    target_mb: float,
    duration: float,
    audio_kbps: float,
    *,
    reserve: float = _TARGET_RESERVE,
) -> int:
    """按十进制 MB 目标计算视频码率，并预留封装开销。"""
    if target_mb <= 0 or duration <= 0:
        raise ValueError("目标体积和时长必须大于 0")
    total_kbps = target_mb * 1_000_000 * 8 * reserve / duration / 1000
    video_kbps = math.floor(total_kbps - audio_kbps)
    if video_kbps < 100:
        raise ValueError(
            "目标体积过小：扣除音频后视频码率低于 100kbps，请增大目标体积或降低音频码率"
        )
    return video_kbps


def calculate_canvas_width(info: BrandMediaInfo, height: int, width: int | None = None) -> int:
    """计算偶数宽度，确保 yuv420p 可以编码。"""
    candidate = width if width is not None else round(info.width * height / info.height)
    if candidate <= 0:
        raise ValueError("输出宽度必须大于 0")
    return candidate if candidate % 2 == 0 else candidate + 1


def calculate_default_watermark_width(width: int, height: int) -> int:
    """Use 30% of the output's shorter edge for a stable logo size."""
    if width <= 0 or height <= 0:
        raise ValueError("输出尺寸必须大于 0")
    return max(1, round(min(width, height) * _DEFAULT_WATERMARK_SHORT_EDGE_RATIO))


def parse_watermark_width(value: str, canvas_width: int) -> int:
    """解析水印宽度，支持像素值或相对画面宽度百分比。"""
    raw = value.strip()
    if raw.endswith("%"):
        try:
            percent = float(raw[:-1])
        except ValueError as exc:
            raise ValueError(f"无效水印宽度：{value}") from exc
        if not 0 < percent <= 100:
            raise ValueError("水印宽度百分比必须在 0～100% 之间")
        return max(1, round(canvas_width * percent / 100))
    try:
        pixels = int(raw)
    except ValueError as exc:
        raise ValueError(f"无效水印宽度：{value}。示例：140 或 15%") from exc
    if pixels <= 0:
        raise ValueError("水印宽度必须大于 0")
    return pixels


def _normalise_video(index: int, label: str, width: int, height: int, fps: int) -> str:
    return (
        f"[{index}:v]scale=w={width}:h={height}:force_original_aspect_ratio=decrease:"
        "force_divisible_by=2:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},setsar=1,setpts=PTS-STARTPTS[{label}]"
    )


def _audio_filter(index: int, label: str, info: BrandMediaInfo) -> str:
    audio_format = "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    if info.has_audio:
        return (
            f"[{index}:a]aresample=44100,atrim=duration={info.duration:.6f},{audio_format},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
    return (
        f"anullsrc=r=44100:cl=stereo,atrim=duration={info.duration:.6f},"
        f"{audio_format},asetpts=PTS-STARTPTS[{label}]"
    )


def _overlay_position(position: str, margin: int) -> tuple[str, str]:
    positions = {
        "top-left": (str(margin), str(margin)),
        "top-right": (f"W-w-{margin}", str(margin)),
        "bottom-left": (str(margin), f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }
    return positions[position]


def _text_watermark_units(text: str) -> float:
    """估算文本视觉宽度，供按对角线覆盖比例计算字号。"""
    return sum(
        1.0 if unicodedata.east_asian_width(character) in {"W", "F"} else 0.6
        for character in text
    )


def _contains_cjk(text: str) -> bool:
    """判断文本是否需要显式选择 CJK 字体。"""
    return any(unicodedata.east_asian_width(character) in {"W", "F"} for character in text)


def resolve_text_watermark_font(font: Path | None, text: str | None) -> Path | None:
    """为 CJK 文字固定选择 skill 附带字体，避免依赖系统字库。"""
    if font is not None or text is None or not _contains_cjk(text):
        return font
    if _BUNDLED_CJK_FONT.is_file():
        return _BUNDLED_CJK_FONT
    raise ValueError(f"技能附带的中文字体缺失：{_BUNDLED_CJK_FONT}")


def _escape_drawtext_value(value: str) -> str:
    """转义 drawtext 过滤器的文本和字体路径值。"""
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _text_watermark_filter(
    text: str,
    *,
    width: int,
    height: int,
    fps: int,
    coverage: float,
    opacity: float,
    font: Path | None,
) -> str:
    """生成居中、沿画面对角线旋转的半透明文字水印。"""
    diagonal = math.hypot(width, height)
    font_size = max(12, round(diagonal * coverage / _text_watermark_units(text)))
    canvas_width = math.ceil(diagonal)
    canvas_height = font_size * 2
    angle = -math.atan2(height, width)
    font_option = f"fontfile='{_escape_drawtext_value(str(font))}':" if font else ""
    return (
        f"color=c=black@0.0:s={canvas_width}x{canvas_height}:r={fps},format=rgba,"
        f"drawtext={font_option}text='{_escape_drawtext_value(text)}':"
        f"fontcolor=white@{opacity:.4f}:fontsize={font_size}:"
        f"borderw=2:bordercolor=black@{opacity * 0.7:.4f}:"
        "x=(w-text_w)/2:y=(h-text_h)/2,"
        f"rotate={angle:.8f}:ow=rotw({angle:.8f}):oh=roth({angle:.8f}):c=none"
        "[text_watermark]"
    )


def build_filter_graph(
    main_info: BrandMediaInfo,
    *,
    width: int,
    height: int,
    fps: int,
    outro_info: BrandMediaInfo | None = None,
    watermark_input_index: int | None = None,
    watermark_width: int = 140,
    watermark_opacity: float = 0.45,
    watermark_position: str = "bottom-left",
    watermark_scope: str = "main",
    margin: int = 20,
    text_watermark: str | None = None,
    text_watermark_coverage: float = 0.8,
    text_watermark_opacity: float = 0.45,
    text_watermark_font: Path | None = None,
    include_audio: bool = True,
) -> tuple[str, str, str | None]:
    """构建滤镜图，返回滤镜、视频标签和可选音频标签。"""
    filters = [_normalise_video(0, "main", width, height, fps)]
    main_label = "[main]"

    if outro_info is not None:
        filters.append(_normalise_video(1, "outro", width, height, fps))

    logo_label = None
    if watermark_input_index is not None:
        filters.append(
            f"[{watermark_input_index}:v]format=rgba,scale={watermark_width}:-1,"
            f"colorchannelmixer=aa={watermark_opacity:.4f}[logo]"
        )
        logo_label = "[logo]"

    if logo_label and watermark_scope == "main":
        x, y = _overlay_position(watermark_position, margin)
        filters.append(
            f"{main_label}{logo_label}overlay=x={x}:y={y}:shortest=1[main_marked]"
        )
        main_label = "[main_marked]"

    if text_watermark is not None:
        filters.append(
            _text_watermark_filter(
                text_watermark,
                width=width,
                height=height,
                fps=fps,
                coverage=text_watermark_coverage,
                opacity=text_watermark_opacity,
                font=text_watermark_font,
            )
        )
        filters.append(
            f"{main_label}[text_watermark]overlay=x=(W-w)/2:y=(H-h)/2:shortest=1"
            "[main_text_marked]"
        )
        main_label = "[main_text_marked]"

    audio_label = None
    video_label = main_label
    if outro_info is not None:
        if include_audio:
            filters.append(_audio_filter(0, "a0", main_info))
            filters.append(_audio_filter(1, "a1", outro_info))
            filters.append(
                f"{main_label}[a0][outro][a1]concat=n=2:v=1:a=1[joinedv][outa]"
            )
            audio_label = "[outa]"
        else:
            filters.append(f"{main_label}[outro]concat=n=2:v=1:a=0[joinedv]")
        video_label = "[joinedv]"

    if logo_label and watermark_scope == "all":
        x, y = _overlay_position(watermark_position, margin)
        filters.append(
            f"{video_label}{logo_label}overlay=x={x}:y={y}:shortest=1[outv]"
        )
        video_label = "[outv]"

    return ";".join(filters), video_label, audio_label


def _input_args(source: Path, outro: Path | None, watermark: Path | None, fps: int) -> list[str]:
    args = ["-i", str(source)]
    if outro is not None:
        args.extend(["-i", str(outro)])
    if watermark is not None:
        args.extend(["-loop", "1", "-framerate", str(fps), "-i", str(watermark)])
    return args


def _encode(
    source: Path,
    output: Path,
    main_info: BrandMediaInfo,
    *,
    outro: Path | None,
    outro_info: BrandMediaInfo | None,
    watermark: Path | None,
    width: int,
    height: int,
    fps: int,
    watermark_width: int,
    watermark_opacity: float,
    watermark_position: str,
    watermark_scope: str,
    margin: int,
    text_watermark: str | None,
    text_watermark_coverage: float,
    text_watermark_opacity: float,
    text_watermark_font: Path | None,
    video_kbps: int,
    audio_bitrate: str,
    preset: str,
    two_pass: bool,
    dry_run: bool,
) -> bool:
    watermark_index = None
    if watermark is not None:
        watermark_index = 2 if outro is not None else 1
    inputs = _input_args(source, outro, watermark, fps)

    def build_args(pass_number: int | None, passlog: str | None) -> list[str]:
        include_audio = pass_number != 1
        graph, video_label, audio_label = build_filter_graph(
            main_info,
            width=width,
            height=height,
            fps=fps,
            outro_info=outro_info,
            watermark_input_index=watermark_index,
            watermark_width=watermark_width,
            watermark_opacity=watermark_opacity,
            watermark_position=watermark_position,
            watermark_scope=watermark_scope,
            margin=margin,
            text_watermark=text_watermark,
            text_watermark_coverage=text_watermark_coverage,
            text_watermark_opacity=text_watermark_opacity,
            text_watermark_font=text_watermark_font,
            include_audio=include_audio,
        )
        args = [*inputs, "-filter_complex", graph, "-map", video_label]
        if pass_number == 1:
            args.append("-an")
        elif outro_info is not None:
            args.extend(["-map", audio_label or "[outa]"])
        elif main_info.has_audio:
            args.extend(["-map", "0:a?"])

        args.extend(
            [
                "-c:v",
                "libx264",
                "-b:v",
                f"{video_kbps}k",
                "-preset",
                preset,
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if pass_number is not None and passlog is not None:
            args.extend(["-pass", str(pass_number), "-passlogfile", passlog])
        if pass_number == 1:
            args.extend(["-f", "mp4", os.devnull])
        else:
            if outro_info is not None or main_info.has_audio:
                args.extend(["-c:a", "aac", "-b:a", audio_bitrate])
            if outro_info is None and main_info.has_audio:
                args.append("-shortest")
            args.extend(["-movflags", "+faststart", str(output)])
        return args

    if not two_pass:
        return run_ffmpeg(build_args(None, None), dry_run=dry_run)

    with tempfile.TemporaryDirectory(prefix="ffmpeg-brand-") as temp_dir:
        passlog = str(Path(temp_dir) / "pass")
        console.print("[cyan]第一遍：分析画面复杂度[/cyan]")
        if not run_ffmpeg(build_args(1, passlog), dry_run=dry_run):
            return False
        console.print("[cyan]第二遍：生成最终文件[/cyan]")
        return run_ffmpeg(build_args(2, passlog), dry_run=dry_run)


@app.command()
def main(
    source: Path = typer.Argument(
        ...,
        help="主视频文件",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="输出文件，默认与源文件同目录并添加 _branded 后缀",
        file_okay=True,
        dir_okay=False,
    ),
    watermark: Path | None = typer.Option(
        None,
        "--watermark",
        "-w",
        help="水印图片，推荐透明背景 PNG",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    outro: Path | None = typer.Option(
        None,
        "--outro",
        help="追加到主视频末尾的片尾视频",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    target_mb: float | None = typer.Option(
        None,
        "--target-mb",
        help="目标体积（十进制 MB）；设置后自动计算码率并使用两遍编码",
    ),
    video_bitrate: str | None = typer.Option(
        None,
        "--video-bitrate",
        "-vb",
        help="视频码率，如 1500k；与 --target-mb 二选一",
    ),
    audio_bitrate: str = typer.Option(
        "128k",
        "--audio-bitrate",
        "-ab",
        help="AAC 音频码率，如 64k、128k",
    ),
    height: int = typer.Option(480, "--height", help="输出高度，默认 480"),
    width: int | None = typer.Option(
        None,
        "--width",
        help="输出宽度；默认按主视频比例自动计算",
    ),
    fps: int = typer.Option(30, "--fps", help="输出帧率，默认 30"),
    watermark_width: str | None = typer.Option(
        None,
        "--watermark-width",
        help="可选的水印宽度覆盖值，支持像素或相对画面宽度，如 140、35%",
    ),
    watermark_opacity: float = typer.Option(
        0.45,
        "--watermark-opacity",
        help="水印整体不透明度，范围 0～1",
    ),
    watermark_position: str = typer.Option(
        "bottom-left",
        "--watermark-position",
        help="top-left / top-right / bottom-left / bottom-right / center",
    ),
    watermark_scope: str = typer.Option(
        "main",
        "--watermark-scope",
        help="main 仅主视频；all 覆盖含片尾的完整视频",
    ),
    margin: int = typer.Option(20, "--margin", help="水印距画面边缘的像素数"),
    text_watermark: str | None = typer.Option(
        None,
        "--text-watermark",
        help="剧中居中对角线文字水印；省略则不添加",
    ),
    text_watermark_coverage: float = typer.Option(
        0.8,
        "--text-watermark-coverage",
        help="文字沿画面对角线的覆盖比例，范围 0～1，默认 0.8",
    ),
    text_watermark_opacity: float = typer.Option(
        0.45,
        "--text-watermark-opacity",
        help="文字水印不透明度，范围 0～1，默认 0.45",
    ),
    text_watermark_font: Path | None = typer.Option(
        None,
        "--text-watermark-font",
        help="文字水印字体文件；中文默认使用 skill 附带的 Source Han Sans SC",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    preset: str = typer.Option(
        "medium",
        "--preset",
        help="libx264 编码预设；slow 更省体积但耗时更长",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示 ffmpeg 命令，不写文件"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="兼容自动化调用；命令本身不会等待 stdin",
    ),
):
    """添加水印和/或片尾，并按分辨率、帧率或目标体积压缩视频。"""
    del non_interactive
    ensure_ffmpeg()

    if watermark is None and outro is None and text_watermark is None:
        console.print("[red]错误：至少指定图片水印、文字水印或片尾之一[/red]")
        raise typer.Exit(1)
    if target_mb is not None and video_bitrate is not None:
        console.print("[red]错误：--target-mb 与 --video-bitrate 不能同时使用[/red]")
        raise typer.Exit(1)
    if height <= 0 or fps <= 0 or margin < 0:
        console.print("[red]错误：高度、帧率必须大于 0，边距不能为负数[/red]")
        raise typer.Exit(1)
    if not 0 < watermark_opacity <= 1:
        console.print("[red]错误：水印不透明度必须在 0～1 之间[/red]")
        raise typer.Exit(1)
    if text_watermark is not None and not text_watermark.strip():
        console.print("[red]错误：文字水印不能为空[/red]")
        raise typer.Exit(1)
    if not 0 < text_watermark_coverage <= 1:
        console.print("[red]错误：文字水印覆盖比例必须在 0～1 之间[/red]")
        raise typer.Exit(1)
    if not 0 < text_watermark_opacity <= 1:
        console.print("[red]错误：文字水印不透明度必须在 0～1 之间[/red]")
        raise typer.Exit(1)
    if watermark_position not in _POSITIONS:
        console.print(f"[red]错误：未知水印位置 {watermark_position}[/red]")
        raise typer.Exit(1)
    if watermark_scope not in _SCOPES:
        console.print(f"[red]错误：未知水印范围 {watermark_scope}[/red]")
        raise typer.Exit(1)
    if preset not in _PRESETS:
        console.print(f"[red]错误：未知编码预设 {preset}[/red]")
        raise typer.Exit(1)

    output = output or source.with_name(f"{source.stem}_branded.mp4")
    if output.resolve() == source.resolve():
        console.print("[red]错误：输出文件不能覆盖源文件[/red]")
        raise typer.Exit(1)

    try:
        resolved_text_font = resolve_text_watermark_font(text_watermark_font, text_watermark)
        main_info = probe_brand_media(source)
        outro_info = probe_brand_media(outro) if outro is not None else None
        canvas_width = calculate_canvas_width(main_info, height, width)
        logo_width = (
            parse_watermark_width(watermark_width, canvas_width)
            if watermark_width is not None
            else calculate_default_watermark_width(canvas_width, height)
        )
        audio_kbps = parse_bitrate_kbps(audio_bitrate)
        normalised_audio_bitrate = f"{audio_kbps:g}k"
        total_duration = main_info.duration + (outro_info.duration if outro_info else 0)
        output_has_audio = outro_info is not None or main_info.has_audio
        if target_mb is not None:
            chosen_video_kbps = calculate_video_bitrate_kbps(
                target_mb,
                total_duration,
                audio_kbps if output_has_audio else 0,
            )
        else:
            chosen_video_kbps = round(parse_bitrate_kbps(video_bitrate or "1500k"))
    except ValueError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from exc

    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
    console.print(f"输出尺寸：[green]{canvas_width}x{height}[/green]")
    console.print(f"输出帧率：[green]{fps} fps[/green]")
    console.print(f"视频码率：[green]{chosen_video_kbps} kbps[/green]")
    if output_has_audio:
        console.print(f"音频码率：[green]{normalised_audio_bitrate}[/green]")
    else:
        console.print("音频：[yellow]源视频无音轨，输出不创建音轨[/yellow]")
    if target_mb is not None:
        console.print(f"目标体积：[green]< {target_mb:g} MB[/green]（预留 5% 封装余量）")

    encode_kwargs = dict(
        outro=outro,
        outro_info=outro_info,
        watermark=watermark,
        width=canvas_width,
        height=height,
        fps=fps,
        watermark_width=logo_width,
        watermark_opacity=watermark_opacity,
        watermark_position=watermark_position,
        watermark_scope=watermark_scope,
        margin=margin,
        text_watermark=text_watermark.strip() if text_watermark is not None else None,
        text_watermark_coverage=text_watermark_coverage,
        text_watermark_opacity=text_watermark_opacity,
        text_watermark_font=resolved_text_font,
        audio_bitrate=normalised_audio_bitrate,
        preset=preset,
        two_pass=target_mb is not None,
        dry_run=dry_run,
    )
    if not _encode(
        source,
        output,
        main_info,
        video_kbps=chosen_video_kbps,
        **encode_kwargs,
    ):
        raise typer.Exit(1)
    if dry_run:
        raise typer.Exit(0)

    if target_mb is not None:
        target_bytes = round(target_mb * 1_000_000)
        actual_bytes = output.stat().st_size
        if actual_bytes > target_bytes:
            retry_kbps = math.floor(chosen_video_kbps * target_bytes / actual_bytes * 0.98)
            if retry_kbps < 100:
                console.print("[red]错误：输出超出目标，且无法继续降低到安全视频码率[/red]")
                raise typer.Exit(1)
            console.print(
                f"[yellow]输出为 {actual_bytes / 1_000_000:.2f} MB，"
                f"自动将视频码率降到 {retry_kbps} kbps 后重试[/yellow]"
            )
            if not _encode(
                source,
                output,
                main_info,
                video_kbps=retry_kbps,
                **encode_kwargs,
            ):
                raise typer.Exit(1)
            actual_bytes = output.stat().st_size
            if actual_bytes > target_bytes:
                console.print(
                    f"[red]错误：重试后仍为 {actual_bytes / 1_000_000:.2f} MB，超过目标[/red]"
                )
                raise typer.Exit(1)

    console.print(
        f"[green]完成：{output}（{output.stat().st_size / 1_000_000:.2f} MB）[/green]"
    )


if __name__ == "__main__":
    app()
