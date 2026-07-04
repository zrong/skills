#!/usr/bin/env python3
"""ffmpeg_merge: 无损视频合并（concat demuxer + -c copy）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import typer

from .common import console, ensure_ffmpeg, probe, run_ffmpeg

app = typer.Typer(help="无损视频合并工具（concat + -c copy）")


def _write_concat_list(files: list[Path]) -> Path:
    """生成 concat demuxer 使用的临时 list 文件。"""
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".txt", mode="w", encoding="utf-8"
    )
    for fp in files:
        # 单引号包裹绝对路径，并转义路径中的单引号
        safe = str(fp.resolve()).replace("'", r"'\''")
        tmp.write(f"file '{safe}'\n")
    tmp.close()
    return Path(tmp.name)


@app.command()
def main(
    files: list[Path] = typer.Argument(
        ..., help="待合并的视频文件（≥2 个）；或单个 concat .txt 列表文件"
    ),
    output: Path = typer.Option(
        Path("merged.mp4"), "--output", "-o", help="输出文件路径"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示命令与一致性检查"),
):
    """合并编码一致的视频文件（无损，不重新编码）。

    示例：
        ffmpeg_merge part1.mp4 part2.mp4 -o merged.mp4
    """
    ensure_ffmpeg()

    # 单个 .txt → 直接用作 concat 输入
    if len(files) == 1 and files[0].suffix.lower() == ".txt":
        list_file = files[0]
        if not list_file.is_file():
            console.print(f"[red]错误：列表文件不存在：{list_file}[/red]")
            raise typer.Exit(1)
        console.print(f"使用 concat 列表：[cyan]{list_file}[/cyan]")
        generated = False
    else:
        if len(files) < 2:
            console.print("[red]错误：合并至少需要 2 个文件[/red]")
            raise typer.Exit(1)
        for f in files:
            if not f.is_file():
                console.print(f"[red]错误：文件不存在：{f}[/red]")
                raise typer.Exit(1)

        # 一致性检查（codec / profile / 分辨率 / pix_fmt）
        infos = [probe(f) for f in files]
        if all(infos):
            fingerprints = {i.digest() for i in infos if i}  # type: ignore[union-attr]
            if len(fingerprints) > 1:
                console.print(
                    "[red]警告：文件编码/分辨率不一致，concat 可能失败或花屏：[/red]"
                )
                for f, i in zip(files, infos):
                    console.print(f"  {f.name}: {i.digest() if i else '未知'}")  # type: ignore[union-attr]
            else:
                console.print(f"[green]✓ {len(files)} 个文件编码一致[/green]")
        else:
            console.print("[yellow]无法探测部分文件的信息，跳过一致性检查[/yellow]")

        list_file = _write_concat_list(files)
        generated = True

    try:
        args = [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", str(output),
        ]
        console.print(f"合并 → [green]{output}[/green]")
        if run_ffmpeg(args, dry_run=dry_run):
            if not dry_run:
                console.print(f"[green]✓ 已保存：{output}[/green]")
        else:
            raise typer.Exit(1)
    finally:
        if generated:
            list_file.unlink(missing_ok=True)


if __name__ == "__main__":
    app()
