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
| `--video` | (必填) | 视频文件路径 |
| `--frames` | `8` | 提取帧数 |
| `--cols` | `4` | spritesheet 列数 |
| `--bg-color` | `auto` | 背景色: `auto` / `green` / `blue` / `white` / `black` |
| `--smart` | 关闭 | 启用视频模型分析最佳循环区间 |
| `--loop-start` | `None` | 手动指定循环起始帧（覆盖自动检测） |
| `--loop-end` | `None` | 手动指定循环结束帧（覆盖自动检测） |
| `--output-dir` | 视频同目录 | 输出目录；未指定时自动在视频同目录下创建 `[视频名]-[w]x[h]-[N]f` |

### 示例

```bash
# 基本用法（自动检测背景色）
uv run spritesheet.py --video video.mp4

# 指定绿幕背景，16 帧
uv run spritesheet.py --video video.mp4 --bg-color green --frames 16

# 使用 AI 分析最佳循环区间
uv run spritesheet.py --video video.mp4 --smart
```

4. **展示结果**：告知用户输出目录，并建议打开 `player.html` 验证动画效果。

## 输出

- `frames/frame_01.png` ~ `frames/frame_NN.png` — 裁切后的独立透明 PNG（每帧）
- `spritesheet.png` — 合并的 spritesheet
- `metadata.json` — 播放元数据（裁切框、循环区间、视频源）
- `player.html` — 可交互的动画播放器（浏览器直接打开）

## 注意事项

- `--smart` 模式需要 `agent_config.toml` 中配置视频分析模型（`video-analyzer.models.{name}`）
- `--smart` 模式根据 API 端点自动选择传入方式：
  - 标准端点 `/api/v3`: 使用 `input_video` 直接传入视频（base64），平台按 fps=5 自动抽帧
  - Coding plan `/api/coding/v3`: 使用 `input_image` 手动抽帧（每秒 5 帧）以图片方式传入
  - API 文档: https://www.volcengine.com/docs/82379/1895586
  - fps 范围: [0.2, 5.0]，默认取最大值 5 以获得最精细的运动分析
  - base64 方式支持 ≤ 50MB 视频；更大文件需改用 Files API
- 背景色建议使用**绿幕**效果最佳（Chroma Key 色相分割精度最高）
- 白幕和黑幕因与主体暗部/高光重叠，边缘可能不如绿幕干净

## 视频生成建议

生成视频时在 prompt 中指定纯色背景，推荐绿幕：

> `pure chroma green background #00FF00, no gradient, no shadows, uniform lighting`

| 背景色 | 色值 | prompt 示例 | 抠图效果 |
|--------|------|-------------|---------|
| 🟢 绿幕（推荐） | `#00FF00` | `pure chroma green background #00FF00` | ✅ 最佳 |
| 🔵 蓝幕 | `#0000FF` | `pure chroma blue background #0000FF` | ✅ 好，蓝色主体冲突时改用绿幕 |
| ⚪ 白幕 | `#FFFFFF` | `pure white background #FFFFFF` | ⚠️ 高光区易混淆 |
| ⚫ 黑幕 | `#000000` | `pure black background #000000` | ⚠️ 暗部易丢失 |
