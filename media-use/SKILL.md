---
name: media-use
description: 媒体处理工具集（基于 ffmpeg）。当用户需要进行视频转码、格式转换、音频处理、视频裁剪/剪辑、视频合并/拼接、修复 m3u 下载的损坏视频（faststart/moov atom）等媒体操作时使用。支持批量视频转码（H.264、H.265、AV1、VP9 + GPU 硬件加速）、按时间段无损裁剪、编码一致视频合并、mp4 moov 前置修复。
---

# Media Use - 媒体处理工具集

基于 ffmpeg 的媒体处理工具集，提供 4 个独立 CLI：转码、裁剪、合并、修复。

## 准备

```bash
cd media-use/scripts
uv sync
```

所有命令通过 `uv run <工具>` 执行。依赖：Python 3.13+、ffmpeg / ffprobe（需预先安装）。

## 工具列表

### ffmpeg_batch —— 批量视频转码

多种编码格式 + GPU 硬件加速，支持递归、并行、预览。

```bash
# 转码为 H.264 4Mbps，音频直接复制
uv run ffmpeg_batch /path/to/source /path/to/target -vc h264 -vb 4M

# NVIDIA GPU 加速转码 H.265
uv run ffmpeg_batch /path/to/source /path/to/target -vc hevc-nvenc -vb 5M --hwaccel-decode

# 查看支持的编码器
uv run ffmpeg_batch --list-codecs
```

常用参数：`-vc/--video-codec`、`-ac/--audio-codec`、`-vb/--video-bitrate`、
`-ab/--audio-bitrate`、`--hwaccel-decode`、`-s/--suffix`、`-r/--recursive`、
`-e/--ext`、`-j/--jobs`、`--dry-run`、`--list-codecs`。

### ffmpeg_cut —— 无损视频裁剪

用 `-c copy` 裁剪片段（不重新编码）。`-s/--start` 与 `-e/--end` 至少指定其一：
省略 `-s` 表示从开头开始，省略 `-e` 表示裁剪到末尾。

```bash
uv run ffmpeg_cut input.mp4 -s 00:01:30 -e 00:03:15 -o clip.mp4   # 指定起止
uv run ffmpeg_cut input.mp4 -s 10                                   # 从 10s 裁到末尾
uv run ffmpeg_cut input.mp4 -e 25                                   # 从开头裁到 25s
```

参数：`-s/--start`、`-e/--end`（均支持 `HH:MM:SS.ms` / `MM:SS` / 秒数，二者至少传一个）、
`-o/--output`（默认 `<stem>_cut.<ext>`）、`--dry-run`。

> 注意：流复制裁剪的起点会对齐到最近的关键帧，开头可能偏差几秒；
> 输出时长以「结束 − 起始」为准。

### ffmpeg_merge —— 无损视频合并

将编码一致的视频文件合并为一个（concat demuxer + `-c copy`）。

```bash
uv run ffmpeg_merge part1.mp4 part2.mp4 -o merged.mp4
uv run ffmpeg_merge list.txt -o merged.mp4   # 也可传 concat 列表文件
```

参数：位置参数 ≥2 个文件（或单个 `.txt` 列表）、`-o/--output`（默认 `merged.mp4`）、
`--dry-run`。合并前自动用 ffprobe 校验编码 / 分辨率一致性，不一致时打印警告。

### ffmpeg_fix —— m3u 视频修复

将 moov atom 移至文件头（faststart），修复 m3u 下载导致无法流式播放的 mp4。

```bash
uv run ffmpeg_fix broken.mp4 -o fixed.mp4        # 单文件
uv run ffmpeg_fix ./downloads -o ./fixed -e mp4  # 文件夹批量
```

参数：`SOURCE`（文件或文件夹）、`-o/--output`、`-e/--ext`（默认 `mp4`）、`--dry-run`。

底层：`ffmpeg -i <input> -c copy -movflags +faststart <output>`

## 注意事项

- 目标文件夹必须为空或不存在（转码 / 批量修复会自动创建）
- GPU 编码器需要对应的硬件和驱动支持
- 裁剪 / 合并 / 修复均使用 `-c copy`（无损、极快），不重新编码
