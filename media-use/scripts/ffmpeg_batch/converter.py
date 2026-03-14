#!/usr/bin/env python3
"""使用 ffmpeg 批量转码视频工具。"""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

app = typer.Typer(help="批量转码视频工具")
console = Console()
print_lock = Lock()  # 用于多线程打印同步

# 常用视频编码器配置
VIDEO_CODECS = {
    # H.264 编码器
    "h264": {"name": "libx264", "desc": "H.264 (CPU)", "type": "cpu"},
    "h264-nvenc": {"name": "h264_nvenc", "desc": "H.264 (NVIDIA GPU)", "type": "gpu"},
    "h264-qsv": {"name": "h264_qsv", "desc": "H.264 (Intel QSV)", "type": "gpu"},
    "h264-vaapi": {"name": "h264_vaapi", "desc": "H.264 (VAAPI)", "type": "gpu"},
    # H.265/HEVC 编码器
    "hevc": {"name": "libx265", "desc": "H.265/HEVC (CPU)", "type": "cpu"},
    "hevc-nvenc": {"name": "hevc_nvenc", "desc": "H.265/HEVC (NVIDIA GPU)", "type": "gpu"},
    "hevc-qsv": {"name": "hevc_qsv", "desc": "H.265/HEVC (Intel QSV)", "type": "gpu"},
    "hevc-vaapi": {"name": "hevc_vaapi", "desc": "H.265/HEVC (VAAPI)", "type": "gpu"},
    # AV1 编码器
    "av1": {"name": "libaom-av1", "desc": "AV1 (CPU，较慢)", "type": "cpu"},
    "av1-svt": {"name": "libsvtav1", "desc": "AV1 (SVT，较快)", "type": "cpu"},
    "av1-nvenc": {"name": "av1_nvenc", "desc": "AV1 (NVIDIA GPU)", "type": "gpu"},
    "av1-qsv": {"name": "av1_qsv", "desc": "AV1 (Intel QSV)", "type": "gpu"},
    "av1-vaapi": {"name": "av1_vaapi", "desc": "AV1 (VAAPI)", "type": "gpu"},
    # VP9 编码器
    "vp9": {"name": "libvpx-vp9", "desc": "VP9 (CPU)", "type": "cpu"},
    "vp9-vaapi": {"name": "vp9_vaapi", "desc": "VP9 (VAAPI)", "type": "gpu"},
}

# 常用音频编码器配置
AUDIO_CODECS = {
    "copy": {"name": "copy", "desc": "直接复制（不重新编码）"},
    "aac": {"name": "aac", "desc": "AAC（通用兼容性最好）"},
    "aac-192": {"name": "aac", "desc": "AAC 192kbps", "bitrate": "192k"},
    "aac-256": {"name": "aac", "desc": "AAC 256kbps", "bitrate": "256k"},
    "mp3": {"name": "libmp3lame", "desc": "MP3"},
    "mp3-192": {"name": "libmp3lame", "desc": "MP3 192kbps", "bitrate": "192k"},
    "mp3-320": {"name": "libmp3lame", "desc": "MP3 320kbps", "bitrate": "320k"},
    "opus": {"name": "libopus", "desc": "Opus（高质量）"},
    "opus-128": {"name": "libopus", "desc": "Opus 128kbps", "bitrate": "128k"},
    "ac3": {"name": "ac3", "desc": "AC3（杜比数字）"},
    "flac": {"name": "flac", "desc": "FLAC（无损）"},
}

# 硬件解码映射
HWACCEL_DECODE_MAP = {
    "h264_nvenc": "cuda",
    "hevc_nvenc": "cuda",
    "av1_nvenc": "cuda",
    "h264_qsv": "qsv",
    "hevc_qsv": "qsv",
    "av1_qsv": "qsv",
    "h264_vaapi": "vaapi",
    "hevc_vaapi": "vaapi",
    "av1_vaapi": "vaapi",
}


def get_video_info(video_path: Path) -> dict | None:
    """使用 ffprobe 获取视频分辨率。"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        width, height = result.stdout.strip().split(",")
        return {"width": int(width), "height": int(height)}
    except (subprocess.CalledProcessError, ValueError):
        return None


def check_codec_available(codec_name: str) -> bool:
    """检查编码器是否可用。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
        return codec_name in result.stdout
    except subprocess.CalledProcessError:
        return False


def get_available_codecs() -> dict:
    """获取所有可用的编码器。"""
    available_video = {}
    available_audio = {}

    for key, config in VIDEO_CODECS.items():
        if check_codec_available(config["name"]):
            available_video[key] = config

    for key, config in AUDIO_CODECS.items():
        if config["name"] == "copy" or check_codec_available(config["name"]):
            available_audio[key] = config

    return {"video": available_video, "audio": available_audio}


def convert_video(
    input_path: Path,
    output_path: Path,
    video_bitrate: str,
    video_codec: str,
    audio_codec: str,
    audio_bitrate: str | None,
    hwaccel_decode: bool = False,
) -> bool:
    """转换单个视频文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建 ffmpeg 命令
    cmd = [
        "ffmpeg",
        "-y",  # 覆盖输出文件
    ]

    # 使用硬件加速解码
    if hwaccel_decode and video_codec in HWACCEL_DECODE_MAP:
        accel = HWACCEL_DECODE_MAP[video_codec]
        if accel == "cuda":
            cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        elif accel == "qsv":
            cmd.extend(["-hwaccel", "qsv"])
        elif accel == "vaapi":
            cmd.extend(["-hwaccel", "vaapi", "-vaapi_device", "/dev/dri/renderD128"])

    cmd.extend(["-i", str(input_path)])

    # 视频编码
    cmd.extend(["-c:v", video_codec])
    if video_bitrate:
        cmd.extend(["-b:v", video_bitrate])

    # 音频编码
    if audio_codec == "copy":
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(["-c:a", audio_codec])
        if audio_bitrate:
            cmd.extend(["-b:a", audio_bitrate])

    # GPU 编码预设
    if "_nvenc" in video_codec:
        cmd.extend(["-preset", "p4"])
    elif video_codec.startswith("lib"):
        cmd.extend(["-preset", "medium"])

    cmd.append(str(output_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


def list_codecs():
    """列出所有支持的编码器。"""
    available = get_available_codecs()

    # 视频编码器表格
    video_table = Table(title="视频编码器")
    video_table.add_column("编码", style="cyan")
    video_table.add_column("描述", style="green")
    video_table.add_column("类型", style="yellow")

    for key, config in available["video"].items():
        video_table.add_row(key, config["desc"], config["type"].upper())

    # 音频编码器表格
    audio_table = Table(title="音频编码器")
    audio_table.add_column("编码", style="cyan")
    audio_table.add_column("描述", style="green")

    for key, config in available["audio"].items():
        audio_table.add_row(key, config["desc"])

    console.print(video_table)
    console.print()
    console.print(audio_table)


@app.command()
def main(
    source: Path = typer.Argument(
        None,
        help="源文件夹，包含待转码的视频文件",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    target: Path = typer.Argument(
        None,
        help="目标文件夹，用于存放转码后的文件",
        file_okay=False,
        dir_okay=True,
    ),
    video_codec: str = typer.Option(
        "h264",
        "--video-codec", "-vc",
        help="视频编码器（使用 --list-codecs 查看列表）",
    ),
    audio_codec: str = typer.Option(
        "copy",
        "--audio-codec", "-ac",
        help="音频编码器（使用 --list-codecs 查看列表）",
    ),
    video_bitrate: str = typer.Option(
        "5M",
        "--video-bitrate", "-vb",
        help="视频码率（如 5M、10M、2.5M）",
    ),
    audio_bitrate: str = typer.Option(
        None,
        "--audio-bitrate", "-ab",
        help="音频码率（如 128k、192k、320k）",
    ),
    hwaccel_decode: bool = typer.Option(
        False,
        "--hwaccel-decode",
        help="使用硬件加速解码",
    ),
    suffix: str = typer.Option(
        "",
        "--suffix", "-s",
        help="输出文件名后缀（可选）",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive", "-r",
        help="递归搜索子文件夹",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="仅显示将要执行的操作，不实际转码",
    ),
    list_codecs_flag: bool = typer.Option(
        False,
        "--list-codecs",
        help="列出所有支持的编码器",
    ),
    ext: str = typer.Option(
        "mp4",
        "--ext", "-e",
        help="要处理的视频文件扩展名（默认 mp4）",
    ),
    jobs: int = typer.Option(
        1,
        "--jobs", "-j",
        help="并行转码任务数（默认 1，串行）",
    ),
):
    """批量转码视频工具。

    支持多种视频和音频编码格式，支持 GPU 硬件加速。

    示例：
        # 转码为 H.264 4Mbps，音频直接复制
        ffmpeg_batch /path/to/source /path/to/target -vc h264 -vb 4M

        # 使用 NVIDIA GPU 加速转码 H.265
        ffmpeg_batch /path/to/source /path/to/target -vc hevc-nvenc -vb 5M --hwaccel-decode

        # 转码为 AV1，音频转码为 Opus
        ffmpeg_batch /path/to/source /path/to/target -vc av1-svt -ac opus -ab 128k

        # 查看支持的编码器列表
        ffmpeg_batch --list-codecs
    """
    # 列出编码器
    if list_codecs_flag:
        list_codecs()
        raise typer.Exit(0)

    # 检查必要参数
    if source is None or target is None:
        console.print("[red]错误：必须指定源文件夹和目标文件夹[/red]")
        console.print("使用 --help 查看帮助")
        raise typer.Exit(1)

    # 验证编码器
    available = get_available_codecs()

    if video_codec not in available["video"]:
        console.print(f"[red]错误：未知的视频编码器 '{video_codec}'[/red]")
        console.print("使用 --list-codecs 查看可用编码器")
        raise typer.Exit(1)

    if audio_codec not in available["audio"]:
        console.print(f"[red]错误：未知的音频编码器 '{audio_codec}'[/red]")
        console.print("使用 --list-codecs 查看可用编码器")
        raise typer.Exit(1)

    # 获取实际编码器名称和配置
    v_codec_name = VIDEO_CODECS[video_codec]["name"]
    v_codec_desc = VIDEO_CODECS[video_codec]["desc"]
    a_codec_config = AUDIO_CODECS[audio_codec]
    a_codec_name = a_codec_config["name"]
    a_codec_desc = a_codec_config["desc"]

    # 如果音频编码器配置中有预设码率，使用预设码率
    if "bitrate" in a_codec_config and audio_bitrate is None:
        audio_bitrate = a_codec_config["bitrate"]

    # 查找所有视频文件
    pattern = f"*.{ext}"
    if recursive:
        video_files = list(source.rglob(pattern))
    else:
        video_files = list(source.glob(pattern))

    if not video_files:
        console.print(f"[yellow]在 {source} 中未找到 {ext} 文件[/yellow]")
        raise typer.Exit(0)

    # 显示配置信息
    console.print(f"[blue]找到 {len(video_files)} 个 {ext} 文件[/blue]")
    console.print(f"视频编码：[green]{video_codec} ({v_codec_desc})[/green]")
    console.print(f"视频码率：[green]{video_bitrate}[/green]")
    console.print(f"音频编码：[green]{audio_codec} ({a_codec_desc})[/green]")
    if audio_bitrate:
        console.print(f"音频码率：[green]{audio_bitrate}[/green]")

    # 显示解码加速信息
    if hwaccel_decode and v_codec_name in HWACCEL_DECODE_MAP:
        accel = HWACCEL_DECODE_MAP[v_codec_name]
        accel_name = {"cuda": "CUDA", "qsv": "QSV", "vaapi": "VAAPI"}.get(accel, accel)
        console.print(f"解码加速：[green]{accel_name}[/green]")

    if dry_run:
        console.print("\n[yellow]预览模式 - 将要转码的文件：[/yellow]")
        for video_file in video_files[:10]:  # 只显示前10个
            if recursive:
                relative_path = video_file.relative_to(source)
                output_path = target / relative_path.parent / f"{video_file.stem}{suffix}.{ext}"
            else:
                output_path = target / f"{video_file.stem}{suffix}.{ext}"
            console.print(f"  {video_file.name} -> {output_path.name}")
        if len(video_files) > 10:
            console.print(f"  ... 还有 {len(video_files) - 10} 个文件")
        raise typer.Exit(0)

    # 目标目录安全检查
    if target.exists():
        # 检查目标目录是否为空
        if any(target.iterdir()):
            console.print(f"[red]错误：目标文件夹不为空：{target}[/red]")
            console.print("请清空目标文件夹或指定一个新的文件夹")
            raise typer.Exit(1)
    else:
        # 目标目录不存在，自动创建
        target.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]已创建目标文件夹：{target}[/green]")

    # 带进度条转码文件
    success_count = 0
    fail_count = 0

    def process_single_file(video_file: Path) -> tuple[str, bool]:
        """处理单个文件，返回 (文件名, 是否成功)"""
        if recursive:
            relative_path = video_file.relative_to(source)
            output_path = target / relative_path.parent / f"{video_file.stem}{suffix}.{ext}"
        else:
            output_path = target / f"{video_file.stem}{suffix}.{ext}"

        success = convert_video(
            video_file, output_path, video_bitrate, v_codec_name,
            a_codec_name, audio_bitrate, hwaccel_decode
        )
        return video_file.name, success

    if jobs > 1:
        # 并行处理
        console.print(f"[cyan]使用 {jobs} 个并行任务转码...[/cyan]")

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {executor.submit(process_single_file, f): f for f in video_files}

            for future in as_completed(futures):
                filename, success = future.result()
                with print_lock:
                    if success:
                        success_count += 1
                        console.print(f"[green]✓[/green] {filename}")
                    else:
                        fail_count += 1
                        console.print(f"[red]✗[/red] {filename}")
    else:
        # 串行处理（原有逻辑）
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]转码中...", total=len(video_files))

            for video_file in video_files:
                # 构建输出路径
                if recursive:
                    relative_path = video_file.relative_to(source)
                    output_path = target / relative_path.parent / f"{video_file.stem}{suffix}.{ext}"
                else:
                    output_path = target / f"{video_file.stem}{suffix}.{ext}"

                progress.update(task, description=f"[cyan]正在转码 {video_file.name}...")

                if convert_video(
                    video_file, output_path, video_bitrate, v_codec_name,
                    a_codec_name, audio_bitrate, hwaccel_decode
                ):
                    success_count += 1
                    console.print(f"[green]✓[/green] {video_file.name}")
                else:
                    fail_count += 1
                    console.print(f"[red]✗[/red] {video_file.name}")

                progress.advance(task)

    # 汇总
    console.print(f"\n[bold]转码完成！[/bold]")
    console.print(f"  [green]成功：{success_count}[/green]")
    if fail_count:
        console.print(f"  [red]失败：{fail_count}[/red]")


if __name__ == "__main__":
    app()
