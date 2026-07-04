# media-use scripts

基于 ffmpeg 的媒体处理工具集，提供 4 个独立 CLI：转码、裁剪、合并、修复。

## 安装

```bash
uv sync
```

## 工具

### ffmpeg_batch —— 批量转码

```bash
# 转码为 H.264 4Mbps，音频直接复制
uv run ffmpeg_batch /path/to/source /path/to/target -vc h264 -vb 4M

# 使用 NVIDIA GPU 加速转码 H.265
uv run ffmpeg_batch /path/to/source /path/to/target -vc hevc-nvenc -vb 5M --hwaccel-decode

# 查看支持的编码器
uv run ffmpeg_batch --list-codecs
```

常用参数：`-vc/--video-codec`、`-ac/--audio-codec`、`-vb/--video-bitrate`、
`-ab/--audio-bitrate`、`--hwaccel-decode`、`-s/--suffix`、`-r/--recursive`、
`-e/--ext`、`-j/--jobs`、`--dry-run`、`--list-codecs`。

### ffmpeg_cut —— 无损裁剪

给定起止时间，用 `-c copy` 裁剪视频片段（不重新编码）。

```bash
# 按时间码裁剪
uv run ffmpeg_cut input.mp4 -s 00:01:30 -e 00:03:15 -o clip.mp4

# 按秒数裁剪
uv run ffmpeg_cut input.mp4 -s 10 -e 25
```

参数：`-s/--start`、`-e/--end`（支持 `HH:MM:SS.ms` / `MM:SS` / 秒数）、
`-o/--output`（默认 `<stem>_cut.<ext>`）、`--dry-run`。

> 注意：流复制裁剪的起点会对齐到最近的关键帧，开头可能偏差几秒；
> 输出时长以「结束 − 起始」为准。

### ffmpeg_merge —— 无损合并

将编码一致的视频文件合并为一个（concat demuxer + `-c copy`）。

```bash
uv run ffmpeg_merge part1.mp4 part2.mp4 -o merged.mp4

# 也可以传入一个 concat 列表文件
uv run ffmpeg_merge list.txt -o merged.mp4
```

参数：位置参数 ≥2 个文件（或单个 `.txt` 列表）、`-o/--output`（默认 `merged.mp4`）、
`--dry-run`。合并前会自动用 ffprobe 校验编码/分辨率一致性，不一致时打印警告。

### ffmpeg_fix —— m3u 视频修复

将 moov atom 移至文件头（faststart），修复 m3u 下载导致无法流式播放的 mp4。

```bash
# 单文件
uv run ffmpeg_fix broken.mp4 -o fixed.mp4

# 文件夹批量
uv run ffmpeg_fix ./downloads -o ./fixed -e mp4
```

参数：`SOURCE`（文件或文件夹）、`-o/--output`、`-e/--ext`（默认 `mp4`）、`--dry-run`。

## 依赖

- Python 3.13+
- ffmpeg / ffprobe（需预先安装）
- uv（Python 包管理器）
