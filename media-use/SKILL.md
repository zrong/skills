---
name: media-use
description: 媒体处理工具集（基于 ffmpeg）。当用户需要视频转码、格式转换、压缩到指定大小、调整分辨率/帧率、添加 Logo 或图片水印、设置水印位置/尺寸/透明度、追加片头片尾、音频处理、视频裁剪/剪辑、合并/拼接、修复 m3u 下载损坏视频（faststart/moov atom）时使用。支持品牌视频一体化生成、目标 MB 两遍编码、H.264/H.265/AV1/VP9 批量转码、无损裁剪与合并、mp4 moov 前置修复。
---

# Media Use - 媒体处理工具集

基于 ffmpeg 的媒体处理工具集，提供 5 个独立 CLI：转码、品牌包装、裁剪、合并、修复。

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

### ffmpeg_brand —— 水印、片尾与目标体积压缩

为单个视频添加图片或文字水印、追加片尾，并统一输出尺寸、帧率和编码。设置
`--target-mb` 时自动按主视频与片尾总时长计算码率，使用 H.264 两遍编码并预留
5% 封装余量；若第一次结果仍超出目标，会自动降低视频码率重试一次。
第一遍编码通过 Python 的 `os.devnull` 使用系统空设备（Windows 为 `NUL`），因此
Windows 可直接使用 `--target-mb`。

```bash
# 480P / 30fps，左下角半透明 Logo，并追加片尾
uv run ffmpeg_brand input.mp4 \
  --watermark logo.png \
  --outro outro.mp4 \
  --height 480 --fps 30 \
  --watermark-position bottom-left \
  --watermark-width 35% \
  --watermark-opacity 0.45 \
  --text-watermark "胡扯AI" \
  -o output.mp4

# 控制在 20MB 内；长视频可降低帧率与音频码率
uv run ffmpeg_brand input.mp4 \
  --watermark logo.png \
  --outro outro.mp4 \
  --target-mb 20 \
  --height 480 --fps 20 \
  --audio-bitrate 64k \
  --preset slow \
  -o output_under20mb.mp4
```

常用参数：`-w/--watermark`、`--outro`、`-o/--output`、`--target-mb`、
`-vb/--video-bitrate`、`-ab/--audio-bitrate`、`--height`、`--width`、`--fps`、
`--watermark-width`（像素或百分比）、`--watermark-opacity`、
`--watermark-position`、`--watermark-scope main|all`、`--margin`、`--preset`、
`--text-watermark`、`--text-watermark-coverage`、`--text-watermark-opacity`、
`--text-watermark-font`、`--dry-run`、`--non-interactive`。

- 默认 `--watermark-scope main`，水印只覆盖主视频，追加的片尾保持原样；使用
  `--watermark-scope all` 可覆盖完整成片。
- Logo 默认宽度为输出画面宽度的 `35%`；可通过 `--watermark-width` 覆盖。
- `--text-watermark` 可选；启用后仅覆盖主视频，默认按画面对角线的 `80%` 计算字号、
  居中并沿左下至右上方向旋转，默认不透明度为 `45%`，带低透明黑色描边以提升可读性。
  可通过覆盖比例、透明度和字体
  参数调整；中文默认固定使用 skill 附带的 Source Han Sans SC Regular，不依赖系统字体。
  使用 `--text-watermark-font` 可显式覆盖。

字体文件位于 `assets/fonts/SourceHanSansSC-Regular.otf`，来自 Adobe 官方 Source Han Sans
2.005R 发布分支，按 SIL Open Font License 1.1 分发；许可证随文件保存在
`assets/fonts/LICENSE-SourceHanSans.txt`。
- 推荐使用透明背景 PNG。JPG 或带实色背景的图片调整透明度时，背景也会一起变淡。
- 若目标体积导致视频码率低于 100kbps，命令会拒绝执行，避免生成不可用成片。
- 主视频或片尾缺少音轨时，拼接段会自动补静音，避免 concat 失败或音画错位。

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
- 水印与片尾会重新编码；在严格目标体积下，时长越长，可分配的视频码率越低
