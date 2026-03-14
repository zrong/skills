---
name: media-use
description: 媒体处理工具集。当用户需要进行视频转码、格式转换、音频处理等媒体操作时使用。支持批量视频转码、多种编码格式（H.264、H.265、AV1、VP9）、GPU 硬件加速（NVIDIA、Intel QSV、VAAPI）。
---

# Media Use - 媒体处理工具集

提供基于 ffmpeg 的媒体处理工具，支持视频转码、格式转换、音频处理等功能。

## 工具列表

### ffmpeg_batch - 批量视频转码工具

基于 ffmpeg 的批量视频转码工具，支持多种编码格式和硬件加速。

**功能特性：**
- 批量转码视频文件
- 支持多种视频编码：H.264、H.265/HEVC、AV1、VP9
- 支持多种音频编码：AAC、MP3、Opus、FLAC、AC3
- GPU 硬件加速：NVIDIA NVENC、Intel QSV、VAAPI
- 硬件解码加速
- 递归处理子文件夹
- 预览模式（dry-run）

## 使用场景

- 批量转码视频到不同格式
- 压缩视频以减少存储空间
- 使用 GPU 加速提升转码速度
- 统一视频编码格式

## 使用方法

### 进入脚本目录

```bash
cd media-use/scripts/ffmpeg_batch
```

### 安装依赖

```bash
uv sync
```

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

## 支持的编码器

### 视频编码器

| 编码 | 描述 | 类型 |
|------|------|------|
| `h264` | H.264 (CPU) | CPU |
| `h264-nvenc` | H.264 (NVIDIA GPU) | GPU |
| `h264-qsv` | H.264 (Intel QSV) | GPU |
| `hevc` | H.265/HEVC (CPU) | CPU |
| `hevc-nvenc` | H.265/HEVC (NVIDIA GPU) | GPU |
| `av1` | AV1 (CPU，较慢) | CPU |
| `av1-svt` | AV1 (SVT，较快) | CPU |
| `av1-nvenc` | AV1 (NVIDIA GPU) | GPU |
| `vp9` | VP9 (CPU) | CPU |

### 音频编码器

| 编码 | 描述 |
|------|------|
| `copy` | 直接复制（不重新编码） |
| `aac` | AAC（通用兼容性最好） |
| `mp3` | MP3 |
| `opus` | Opus（高质量） |
| `flac` | FLAC（无损） |
| `ac3` | AC3（杜比数字） |

## 依赖

- Python 3.13+
- ffmpeg（需预先安装）
- uv（Python 包管理器）

## 注意事项

- 目标文件夹必须为空，或不存在（会自动创建）
- 使用 GPU 编码器需要对应的硬件和驱动支持
- 大文件转码可能需要较长时间
