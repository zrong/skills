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
| `--canvas-size` | `512` | 输出帧画布尺寸（正方形） |
| `--bg-color` | `auto` | 背景色: `auto`/`green`/`blue`/`white`/`black` |
| `--smart` | 关闭 | 启用视频模型分析最佳循环区间 |
| `--output-dir` | `./spritesheet_output` | 输出目录 |

### 示例

```bash
# 基本用法（自动检测背景色）
uv run spritesheet.py --video video.mp4

# 指定绿幕背景，16 帧
uv run spritesheet.py --video video.mp4 --bg-color green --frames 16

# 使用 AI 分析最佳循环区间
uv run spritesheet.py --video video.mp4 --smart

# 指定输出目录和画布尺寸
uv run spritesheet.py --video video.mp4 --output-dir ./my_output --canvas-size 256
```

4. **展示结果**：告知用户输出目录，并建议打开 `player.html` 验证动画效果。

## 输出

- `frame_01.png` ~ `frame_NN.png` — 独立透明 PNG（每帧）
- `spritesheet.png` — 合并的 spritesheet
- `player.html` — 可交互的动画播放器（浏览器直接打开）

## 注意事项

- `--smart` 模式需要 `agent_config.toml` 中配置视频分析模型
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
