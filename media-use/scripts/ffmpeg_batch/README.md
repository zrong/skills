# ffmpeg_batch

基于 ffmpeg 的批量视频转码工具。

## 功能特性

- 批量转码视频文件
- 支持多种视频编码：H.264、H.265/HEVC、AV1、VP9
- 支持多种音频编码：AAC、MP3、Opus、FLAC、AC3
- GPU 硬件加速：NVIDIA NVENC、Intel QSV、VAAPI
- 硬件解码加速
- 递归处理子文件夹
- 预览模式（dry-run）

## 安装

```bash
uv sync
```

## 使用

### 查看支持的编码器

```bash
uv run ffmpeg_batch --list-codecs
```

### 基本转码

```bash
# 转码为 H.264 4Mbps，音频直接复制
uv run ffmpeg_batch /path/to/source /path/to/target -vc h264 -vb 4M

# 使用 NVIDIA GPU 加速转码 H.265
uv run ffmpeg_batch /path/to/source /path/to/target -vc hevc-nvenc -vb 5M --hwaccel-decode

# 转码为 AV1，音频转码为 Opus
uv run ffmpeg_batch /path/to/source /path/to/target -vc av1-svt -ac opus -ab 128k
```

## CLI 参数

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `source` | - | 源文件夹路径 | 必需 |
| `target` | - | 目标文件夹路径 | 必需 |
| `--video-codec` | `-vc` | 视频编码器 | h264 |
| `--audio-codec` | `-ac` | 音频编码器 | copy |
| `--video-bitrate` | `-vb` | 视频码率 | 5M |
| `--audio-bitrate` | `-ab` | 音频码率 | - |
| `--hwaccel-decode` | - | 使用硬件解码加速 | false |
| `--suffix` | `-s` | 输出文件名后缀 | - |
| `--recursive` | `-r` | 递归处理子文件夹 | false |
| `--dry-run` | - | 预览模式，不实际转码 | false |
| `--ext` | `-e` | 视频文件扩展名 | mp4 |
| `--list-codecs` | - | 列出支持的编码器 | - |

## 依赖

- Python 3.13+
- ffmpeg（需预先安装）
