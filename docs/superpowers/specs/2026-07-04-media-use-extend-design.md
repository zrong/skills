# media-use skill 扩展设计：视频裁剪 / 合并 / m3u 修复

- 日期：2026-07-04
- 状态：已批准（方案 A）
- 范围：为 media-use skill 新增三个 ffmpeg 工具，并重构为统一包结构

## Context（背景）

media-use skill 当前只有一个工具 `ffmpeg_batch`（批量转码）。用户提出三个新需求：

1. 给定起止时间，对视频做**无损裁剪**
2. 将两个编码/码率相同的视频**合并**为一个
3. 用 `ffmpeg -i input -c copy -movflags +faststart output` **修复** m3u 下载的损坏视频（moov atom 后置导致无法流式播放）

这三个功能与"批量转码"是不同职责。经评审确定：新增独立 CLI 工具（与 ffmpeg_batch 并列），并采用统一的 Python 包结构共享公共代码（方案 A）。

## 目标 / 非目标

**目标**

- 新增 `ffmpeg_cut`、`ffmpeg_merge`、`ffmpeg_fix` 三个独立 CLI
- 重构为单一 uv 项目 + `media_use` 包，公共逻辑下沉到 `common.py`
- 保持 `ffmpeg_batch` 对外行为完全不变（命令名、参数、行为）
- 遵守 CLAUDE.md：非交互、uv 运行、无硬编码密钥、skills.sh 兼容

**非目标**

- 不实现重编码精确裁剪（用户明确选择纯无损 `-c copy`）
- 不对裁剪做批量（YAGNI，时段因片而异）
- 不引入 GUI、不做视频内容分析

## 目录结构（方案 A）

```text
media-use/scripts/
├── pyproject.toml          # 统一依赖，注册 4 个 [project.scripts]
├── uv.lock
├── README.md
└── media_use/
    ├── __init__.py
    ├── common.py           # probe / run_ffmpeg / parse_time / console / ensure_ffmpeg
    ├── convert.py          # ffmpeg_batch（迁移自现有 converter.py）
    ├── cut.py              # ffmpeg_cut（新）
    ├── merge.py            # ffmpeg_merge（新）
    └── fix.py              # ffmpeg_fix（新）
```

`pyproject.toml` 入口：

```toml
[project.scripts]
ffmpeg_batch = "media_use.convert:app"
ffmpeg_cut   = "media_use.cut:app"
ffmpeg_merge = "media_use.merge:app"
ffmpeg_fix   = "media_use.fix:app"
```

## 公共模块 common.py

| 符号 | 说明 |
|------|------|
| `console: Console` | 共享 rich Console（多线程打印锁） |
| `ensure_ffmpeg() -> None` | 启动检查 ffmpeg/ffprobe，缺失则清晰报错退出 |
| `probe(path) -> MediaInfo` | ffprobe 探测，返回 codec/profile/分辨率/码率/pix_fmt/r_frame_rate 等；扩展自现有 `get_video_info` |
| `run_ffmpeg(args, *, dry_run=False) -> bool` | subprocess 封装；dry-run 时只打印命令不执行 |
| `parse_time(s: str) -> str` | 校验并透传时间字符串（`HH:MM:SS.ms` / `MM:SS` / 秒数），非法格式报错 |

## 工具接口

### ffmpeg_cut —— 无损裁剪（单文件）

```
ffmpeg_cut INPUT -s START -e END [-o OUTPUT] [--dry-run]
```

- `INPUT`：输入视频文件路径
- `-s/--start`、`-e/--end`：必填，时间字符串（`parse_time` 校验）
- `-o/--output`：可选，默认 `<stem>_cut.<ext>`
- `--dry-run`：只打印 ffmpeg 命令

底层命令（`-ss` 作 input option 快速 seek；`-to` 作 output option 按原始时间轴停止——ffmpeg 官方推荐的无损裁剪写法）：

```
ffmpeg -ss <START> -i <INPUT> -to <END> -c copy <OUTPUT>
```

### ffmpeg_merge —— 无损合并（多文件 ≥2）

```
ffmpeg_merge FILE [FILE ...] [-o OUTPUT] [--dry-run]
```

- 位置参数：≥2 个文件；也支持传入一个 `.txt` concat 列表文件
- `-o/--output`：可选，默认 `merged.mp4`
- `--dry-run`：只打印将执行的命令与一致性检查结果

流程：

1. `probe()` 逐一检查 codec/profile/分辨率/pix_fmt；不一致打印红色警告（仍继续，因用户声明相同；concat 真正失败时 ffmpeg 会报错并如实转达）
2. 生成临时 concat list 文件
3. `ffmpeg -f concat -safe 0 -i list.txt -c copy <OUTPUT>`

### ffmpeg_fix —— m3u 视频修复（批量）

```
ffmpeg_fix SOURCE [-o TARGET] [-e EXT] [--dry-run]
```

- `SOURCE`：文件或文件夹（文件夹则遍历 `*.{ext}` 批量）
- `-o/--output`：文件夹时默认 `<SOURCE>_fixed/`，单文件时默认 `<stem>_fixed.<ext>`
- `-e/--ext`：默认 `mp4`
- 复用现有"目标目录非空则报错"安全检查

底层（用户给定命令）：`ffmpeg -i <INPUT> -c copy -movflags +faststart <OUTPUT>`

### ffmpeg_batch —— 迁移（行为不变）

`converter.py` → `media_use/convert.py`：业务逻辑零改动，仅把子进程段与 `get_video_info` 替换为调用 `common.py` 的 `run_ffmpeg` / `probe`。命令名与全部参数（`-vc/-ac/-vb/-ab/--hwaccel-decode/-s/-r/--dry-run/-e/-j/--list-codecs`）保持不变。

## 文档更新

- **SKILL.md**：工具列表 1 → 4；`description` 补"裁剪/合并/修复 m3u 视频"等触发词；使用说明里 `cd media-use/scripts/ffmpeg_batch` → `cd media-use/scripts`
- **scripts/README.md**：新增三个工具的使用说明与参数表
- **项目根 README.md**：底部"更新记录"追加本次变更摘要（遵循 CLAUDE.md 工作流）

## 错误处理

- 全部非交互（typer 默认满足 `--non-interactive`）
- `ensure_ffmpeg()` 在每个工具启动时校验依赖
- 目标目录非空检查（fix / convert 复用）
- ffmpeg 非零退出时打印 stderr 摘要

## 验证（end-to-end）

```bash
cd media-use/scripts && uv sync

# 回归：ffmpeg_batch 行为不变
uv run ffmpeg_batch test_source -vc h264 -vb 2M --dry-run

# 新工具
uv run ffmpeg_cut test_source/sample1.mp4 -s 1 -e 3 -o /tmp/cut.mp4
cp test_source/sample1.mp4 /tmp/sample2.mp4
uv run ffmpeg_merge test_source/sample1.mp4 /tmp/sample2.mp4 -o /tmp/merged.mp4
uv run ffmpeg_fix test_source -o /tmp/fixed
```

预期：四个命令均成功，输出文件可正常播放；`ffmpeg_batch` 参数与行为和迁移前完全一致。

## 开放问题

无。裁剪精度已定为纯无损 `-c copy`；批量策略已定（cut 单文件、fix 批量、merge 多文件）；目录结构已选方案 A。
