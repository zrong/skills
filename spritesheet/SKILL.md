---
name: spritesheet
description: 从视频中提取帧生成 spritesheet 和独立透明 PNG。当用户说"制作 spritesheet"、"视频转精灵图"、"提取动画帧"、"sprite sheet"、"循环动画"时使用。
---

# Spritesheet Generator

从视频中提取帧，去背景、对齐、输出透明 PNG 和 spritesheet 动画图，并生成可交互的播放器用于验证效果。

## 使用场景

- 用户需要从视频中提取动画帧制作 spritesheet
- 用户需要生成游戏用的精灵图（sprite animation）
- 用户需要制作循环动画并验证效果
- 用户有绿幕/蓝幕/白幕视频，需要抠图提取主体

> 详细抠图与裁剪流程（每步输入/输出、算子参数、设计意图）见 [references/pipeline.md](references/pipeline.md)。

## 工作流程

1. **确认视频来源**：获取用户提供的视频路径。
2. **确认需求**：帧数、画布尺寸、背景色类型等。如果 `$ARGUMENTS` 非空，解析其中的参数。
3. **执行处理**：运行脚本：

```bash
cd scripts && uv run spritesheet.py --video <视频路径> [选项]
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--video` | (必填*) | 视频文件路径（*`--repack-dir` 模式可不填） |
| `--frames` | `8` | 提取帧数 |
| `--cols` | `4` | spritesheet 列数 |
| `--bg-color` | `auto` | 背景色: `auto` / `green` / `blue` / `white` / `black` |
| `--loop-start` | `None` | 手动指定循环起始帧（最高优先级，需与 `--loop-end` 同时给） |
| `--loop-end` | `None` | 手动指定循环结束帧 |
| `--from-frame-zero` | 关闭 | 强制循环包含第 0 帧（旧 CV 帧差法，回退用） |
| `--output-dir` | 视频同目录 | 输出目录；未指定时自动创建 `[视频名]-[w]x[h]-[N]f` |
| `--analyze` | 关闭 | 诊断模式：输出 `.analysis.json`，不生成 spritesheet |
| `--txt` | 关闭 | 配合 `--analyze` 打印 ASCII 摘要 |
| `--repack-dir` | `None` | 重打包模式：指定已有输出目录重新组合帧（与视频管线互斥） |
| `--drop-frames` | `None` | 重打包删除帧（1-based，如 `15,16` 或 `15-16`） |
| `--keep-frames` | `None` | 重打包保留帧（1-based，如 `1-14`；与 `--drop-frames` 互斥） |

**循环检测优先级**（正常管线内）：`--loop-start/--loop-end`（手动）> `--from-frame-zero` > **主体感知全局接缝检测（默认）**。默认算法在主体 mask 内全局搜索最优循环接缝，突破旧法两个局限：① 不假设循环从第 0 帧开始（能处理行走等"首帧是静态空闲态"的动画）；② MSE 只在主体区域计算（避免背景稀释造成的误差）。自动结果不可靠时，用 `--analyze` 查看周期候选与质心/大小趋势，再手动指定 `--loop-start/--loop-end`。

### 示例

```bash
# 基本用法（自动检测背景色）
uv run spritesheet.py --video video.mp4

# 指定绿幕背景，16 帧
uv run spritesheet.py --video video.mp4 --bg-color green --frames 16

# 自动检测不可靠时：先诊断，再手动指定区间
uv run spritesheet.py --video video.mp4 --analyze --txt
uv run spritesheet.py --video video.mp4 --loop-start 0 --loop-end 30

# 基于已生成的帧删帧重拼（不重跑抠图）
uv run spritesheet.py --repack-dir ./xxx-420x420-16f --drop-frames 15,16
```

4. **展示结果**：告知用户输出目录，并建议打开 `player.html` 验证动画效果。

## 输出

- `frames/frame_01.png` ~ `frames/frame_NN.png` — 裁切后的独立透明 PNG（每帧）
- `spritesheet.png` — 合并的 spritesheet
- `metadata.json` — 播放元数据（裁切框、循环区间、视频源）
- `player.html` — 可交互的动画播放器（浏览器直接打开）
- `<视频名>.analysis.json` — 仅 `--analyze` 模式生成，循环分析报告（MSE 曲线、周期候选、质心轨迹、主体大小趋势）

## 注意事项

- 需要大模型辅助判断循环区间时，使用 video-analyzer（见其 `references/loop-analysis.md`），再用 `--loop-start/--loop-end` 应用
- 背景色建议使用**绿幕**效果最佳（Chroma Key 色相分割精度最高）
- 白幕和黑幕因与主体暗部/高光重叠，边缘可能不如绿幕干净
- 默认循环检测已从"CV 帧差法"升级为"主体感知全局接缝检测"（`metadata.loop.method` 由 `cv` 变为 `global`）；主体分离失败（mask 占比过低）时会自动回退全画面法并提示

## 视频生成建议

生成视频时在 prompt 中指定纯色背景，推荐绿幕：

> `pure chroma green background #00FF00, no gradient, no shadows, uniform lighting`

| 背景色 | 色值 | prompt 示例 | 抠图效果 |
|--------|------|-------------|---------|
| 🟢 绿幕（推荐） | `#00FF00` | `pure chroma green background #00FF00` | ✅ 最佳 |
| 🔵 蓝幕 | `#0000FF` | `pure chroma blue background #0000FF` | ✅ 好，蓝色主体冲突时改用绿幕 |
| ⚪ 白幕 | `#FFFFFF` | `pure white background #FFFFFF` | ⚠️ 高光区易混淆 |
| ⚫ 黑幕 | `#000000` | `pure black background #000000` | ⚠️ 暗部易丢失 |
