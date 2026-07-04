# media-use scripts

基于 ffmpeg 的媒体处理工具集。当前提供 ffmpeg_batch 批量转码工具。

## 安装

```bash
uv sync
```

## ffmpeg_batch —— 批量转码

```bash
# 转码为 H.264 4Mbps，音频直接复制
uv run ffmpeg_batch /path/to/source /path/to/target -vc h264 -vb 4M

# 查看支持的编码器
uv run ffmpeg_batch --list-codecs
```

常用参数：`-vc/--video-codec`、`-ac/--audio-codec`、`-vb/--video-bitrate`、
`-ab/--audio-bitrate`、`--hwaccel-decode`、`-s/--suffix`、`-r/--recursive`、
`-e/--ext`、`-j/--jobs`、`--dry-run`、`--list-codecs`。

## 依赖

- Python 3.13+
- ffmpeg / ffprobe（需预先安装）
- uv（Python 包管理器）
