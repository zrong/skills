# media-use scripts

基于 ffmpeg 的媒体处理工具集，提供 5 个独立 CLI：转码、品牌包装、裁剪、合并、修复。

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

### ffmpeg_brand —— 图片/文字水印、片尾与目标体积压缩

```bash
# 左下角 Logo + 片尾，输出 480P / 30fps
uv run ffmpeg_brand input.mp4 \
  --watermark logo.png \
  --outro outro.mp4 \
  --height 480 --fps 30 \
  --watermark-width 35% \
  --watermark-opacity 0.45 \
  --text-watermark "胡扯AI" \
  -o output.mp4

# 目标小于 20MB，自动计算视频码率并执行两遍编码
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
`--watermark-width`、`--watermark-opacity`、`--watermark-position`、
`--watermark-scope main|all`、`--margin`、`--text-watermark`、
`--text-watermark-coverage`、`--text-watermark-opacity`、`--text-watermark-font`、
`--preset`、`--dry-run`、`--non-interactive`。

`--target-mb` 使用十进制 MB，自动预留 5% 封装余量。默认只给主视频加水印；
`--watermark-scope all` 会让水印覆盖追加片尾。片尾没有音轨时会自动补静音。
Logo 默认宽度为输出画面宽度的 `35%`；可通过 `--watermark-width` 覆盖。
`--text-watermark` 可选；启用后仅覆盖主视频，默认按画面对角线的 `80%` 计算字号、
居中并沿左下至右上方向旋转，默认不透明度为 `45%`，带低透明黑色描边以提升可读性。
中文默认固定使用 skill 附带的 Source Han Sans SC Regular，不依赖系统字体；使用
`--text-watermark-font` 可显式覆盖。字体文件位于
`../assets/fonts/SourceHanSansSC-Regular.otf`，许可证见
`../assets/fonts/LICENSE-SourceHanSans.txt`。

### ffmpeg_cut —— 无损裁剪

用 `-c copy` 裁剪视频片段（不重新编码）。`-s/--start` 与 `-e/--end` 至少指定其一：
省略 `-s` 表示从开头开始，省略 `-e` 表示裁剪到末尾。

```bash
# 指定起止时间
uv run ffmpeg_cut input.mp4 -s 00:01:30 -e 00:03:15 -o clip.mp4

# 从第 10s 裁到末尾
uv run ffmpeg_cut input.mp4 -s 10

# 从开头裁到第 25s
uv run ffmpeg_cut input.mp4 -e 25
```

参数：`-s/--start`、`-e/--end`（均支持 `HH:MM:SS.ms` / `MM:SS` / 秒数，二者至少传一个）、
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
