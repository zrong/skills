#!/usr/bin/env python3
"""ffmpeg_fix: 修复 m3u 下载的视频（-c copy -movflags +faststart）。"""

from __future__ import annotations

from pathlib import Path

import typer

from .common import console, ensure_ffmpeg, prepare_target_dir, run_ffmpeg

app = typer.Typer(help="m3u 视频修复工具（faststart）")


def _fix_one(src: Path, dst: Path, dry_run: bool, *, quiet: bool = False) -> bool:
    """修复单个文件：将 moov atom 移至文件头（faststart）。"""
    if not quiet:
        console.print(f"修复 [cyan]{src.name}[/cyan] → [green]{dst}[/green]")
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)]
    return run_ffmpeg(args, dry_run=dry_run)


@app.command()
def main(
    source: Path = typer.Argument(
        ..., help="输入文件或文件夹（文件夹则批量修复）", exists=True
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="输出文件 / 文件夹"
    ),
    ext: str = typer.Option("mp4", "--ext", "-e", help="视频扩展名（文件夹模式）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示 ffmpeg 命令"),
):
    """修复 m3u 下载的 mp4（将 moov atom 移至文件头，使流式播放可用）。

    示例：
        ffmpeg_fix broken.mp4 -o fixed.mp4
        ffmpeg_fix ./downloads -o ./fixed -e mp4
    """
    ensure_ffmpeg()

    # 单文件模式
    if source.is_file():
        out_file = output if output is not None else source.with_name(
            f"{source.stem}_fixed{source.suffix}"
        )
        if _fix_one(source, out_file, dry_run):
            if not dry_run:
                console.print(f"[green]✓ 已保存：{out_file}[/green]")
        else:
            raise typer.Exit(1)
        return

    # 文件夹批量模式
    if output is None:
        output = source.parent / f"{source.name}_fixed"
    prepare_target_dir(output)

    files = sorted(source.glob(f"*.{ext}"))
    if not files:
        console.print(f"[yellow]在 {source} 中未找到 {ext} 文件[/yellow]")
        raise typer.Exit(0)

    console.print(f"[blue]找到 {len(files)} 个 {ext} 文件，开始修复...[/blue]")
    ok = fail = 0
    for f in files:
        out_file = output / f.name
        if _fix_one(f, out_file, dry_run, quiet=True):
            ok += 1
            console.print(f"[green]✓[/green] {f.name}")
        else:
            fail += 1
            console.print(f"[red]✗[/red] {f.name}")

    console.print(f"\n[bold]修复完成[/bold]：成功 [green]{ok}[/green]"
                  + (f"，失败 [red]{fail}[/red]" if fail else ""))


if __name__ == "__main__":
    app()
