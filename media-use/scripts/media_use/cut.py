#!/usr/bin/env python3
"""ffmpeg_cut: 无损视频裁剪（-c copy，不重新编码）。"""

from __future__ import annotations

from pathlib import Path

import typer

from .common import console, ensure_ffmpeg, parse_time, run_ffmpeg, time_to_seconds

app = typer.Typer(help="无损视频裁剪工具（-c copy）")


@app.command()
def main(
    input_file: Path = typer.Argument(
        ..., help="输入视频文件", exists=True, file_okay=True, dir_okay=False
    ),
    start: str = typer.Option(
        ..., "--start", "-s", help="起始时间（HH:MM:SS[.ms] / MM:SS / 秒数）"
    ),
    end: str = typer.Option(
        ..., "--end", "-e", help="结束时间（HH:MM:SS[.ms] / MM:SS / 秒数）"
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="输出文件（默认 <stem>_cut.<ext>）"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示 ffmpeg 命令"),
):
    """无损裁剪视频的指定片段（-c copy，不重新编码）。

    注意：流复制裁剪的起点会对齐到最近的关键帧，开头可能偏差几秒；
    输出时长以「结束 − 起始」为准。

    示例：
        ffmpeg_cut input.mp4 -s 00:01:30 -e 00:03:15 -o clip.mp4
        ffmpeg_cut input.mp4 -s 10 -e 25
    """
    ensure_ffmpeg()

    try:
        start_t = parse_time(start)
        end_t = parse_time(end)
        start_sec = time_to_seconds(start_t)
        end_sec = time_to_seconds(end_t)
    except ValueError as e:
        console.print(f"[red]错误：{e}[/red]")
        raise typer.Exit(1)

    if end_sec <= start_sec:
        console.print("[red]错误：结束时间必须大于起始时间[/red]")
        raise typer.Exit(1)
    duration = end_sec - start_sec

    if output is None:
        output = input_file.with_name(f"{input_file.stem}_cut{input_file.suffix}")

    # -ss 作 input option（快速 seek，会对齐到最近的关键帧）；
    # 用 -t（精确持续时长）而非 -to，避免与 input seeking 组合时的时间轴语义歧义
    args = ["-ss", f"{start_sec}", "-i", str(input_file), "-t", f"{duration}",
            "-c", "copy", str(output)]
    console.print(
        f"裁剪 [cyan]{input_file.name}[/cyan] [{start_t} → {end_t}，约 {duration:.3f}s] → [green]{output}[/green]"
    )
    if run_ffmpeg(args, dry_run=dry_run):
        if not dry_run:
            console.print(f"[green]✓ 已保存：{output}[/green]")
    else:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
