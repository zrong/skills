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
        None, "--start", "-s", help="起始时间（HH:MM:SS[.ms] / MM:SS / 秒数），省略则从开头开始"
    ),
    end: str = typer.Option(
        None, "--end", "-e", help="结束时间（HH:MM:SS[.ms] / MM:SS / 秒数），省略则裁剪到末尾"
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="输出文件（默认 <stem>_cut.<ext>）"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示 ffmpeg 命令"),
):
    """无损裁剪视频的指定片段（-c copy，不重新编码）。

    --start 与 --end 至少指定其一：省略 --start 表示从开头开始，
    省略 --end 表示裁剪到末尾。

    注意：流复制裁剪的起点会对齐到最近的关键帧，开头可能偏差几秒；
    两端都给定时，输出时长以「结束 − 起始」为准。

    示例：
        # 指定起止
        ffmpeg_cut input.mp4 -s 00:01:30 -e 00:03:15 -o clip.mp4
        # 从第 10s 裁到末尾
        ffmpeg_cut input.mp4 -s 10
        # 从开头裁到第 25s
        ffmpeg_cut input.mp4 -e 25
    """
    ensure_ffmpeg()

    if start is None and end is None:
        console.print("[red]错误：--start 与 --end 至少需要指定其中一个[/red]")
        raise typer.Exit(1)

    try:
        start_t = parse_time(start) if start is not None else None
        end_t = parse_time(end) if end is not None else None
    except ValueError as e:
        console.print(f"[red]错误：{e}[/red]")
        raise typer.Exit(1)

    start_sec = time_to_seconds(start_t) if start_t is not None else None
    end_sec = time_to_seconds(end_t) if end_t is not None else None

    if start_sec is not None and end_sec is not None and end_sec <= start_sec:
        console.print("[red]错误：结束时间必须大于起始时间[/red]")
        raise typer.Exit(1)

    if output is None:
        output = input_file.with_name(f"{input_file.stem}_cut{input_file.suffix}")

    # 构造 ffmpeg 参数：
    #   - -ss 作 input option（快速 seek，对齐到最近关键帧），仅当指定 start
    #   - 持续时长统一用 -t（而非 -to，规避与 input seeking 组合的时间轴歧义）：
    #     两端都给 → end-start；仅给 end → end（从头持续 end 秒）；仅给 start → 不加 -t，自然到末尾
    args: list[str] = []
    if start_sec is not None:
        args += ["-ss", f"{start_sec}"]
    args += ["-i", str(input_file)]
    if end_sec is not None:
        duration = end_sec if start_sec is None else end_sec - start_sec
        args += ["-t", f"{duration}"]
    args += ["-c", "copy", str(output)]

    range_start = start_t if start_t is not None else "开头"
    range_end = end_t if end_t is not None else "末尾"
    if start_sec is not None and end_sec is not None:
        duration_hint = f"，约 {end_sec - start_sec:.3f}s"
    elif end_sec is not None:
        duration_hint = f"，约 {end_sec:.3f}s"
    else:
        duration_hint = ""
    console.print(
        f"裁剪 [cyan]{input_file.name}[/cyan] [{range_start} → {range_end}{duration_hint}] → [green]{output}[/green]"
    )
    if run_ffmpeg(args, dry_run=dry_run):
        if not dry_run:
            console.print(f"[green]✓ 已保存：{output}[/green]")
    else:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
