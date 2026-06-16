---
name: ui-extractor
description: 从静态图片中提取前景元素并分离 UI 组件，支持棋盘格背景去除、绿幕/蓝幕/白幕/黑幕关键色抠图、透视校正。当用户说"提取 UI 元素"、"分离组件"、"去除背景"、"棋盘格背景"、"绿幕抠图"、"checkerboard 抠图"、"抠图"时使用。
---

# UI Extractor

从静态图片中自动识别背景类型（棋盘格 / 关键色），去除背景，分离独立的 UI 组件，输出透明 PNG。

## 触发场景

- 需要从带棋盘格背景的 UI 设计图中提取组件
- 需要从绿幕/蓝幕/白幕/黑幕拍摄图中抠出主体
- 需要将 UI 套件拆分为独立的组件图片
- 需要校正倾斜拍摄的棋盘格图片

## 工作流程

1. **确认图片路径**：用户提供带背景的图片路径。
2. **确认需求**：背景类型（auto/chroma/checkerboard）、是否分离元素、是否做透视校正。如果 `$ARGUMENTS` 非空，解析其中的参数。
3. **执行处理**：

```bash
cd scripts && uv run ui_extractor.py --input <图片路径> [选项]
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input` / `-i` | (必填) | 输入图片路径 |
| `--output-dir` / `-o` | 输入同目录 | 输出目录 |
| `--bg-type` | `auto` | 背景类型: `auto` / `chroma` / `checkerboard` |
| `--bg-color` | `auto` | chroma 模式背景色: `auto` / `green` / `blue` / `white` / `black` |
| `--pattern-size` | `9 6` | 棋盘格内角点 (cols rows) |
| `--min-element-area` | `500` | 元素最小面积（像素²） |
| `--no-extract` | `false` | 只去背景，不分离元素 |
| `--warp` | `false` | 启用透视校正（仅 checkerboard 模式） |
| `--debug` | `false` | 输出调试图（mask/标注） |
| `--non-interactive` | `false` | 强制非交互模式 |

### 示例

```bash
# 自动检测背景类型
uv run ui_extractor.py --input ui-design.png

# 明确指定绿幕背景
uv run ui_extractor.py --input photo.jpg --bg-type chroma --bg-color green

# 只去背景不分离元素
uv run ui_extractor.py --input photo.jpg --no-extract

# 棋盘格 + 透视校正
uv run ui_extractor.py --input tilted.png --bg-type checkerboard --warp

# 调试模式
uv run ui_extractor.py --input photo.jpg --debug
```

4. **展示结果**：告知用户输出目录，并展示分离的 UI 组件。

## 输出文件

```
<output-dir>/
├── <name>-nobg.png        # 去背景后的完整图（BGRA）
├── <name>-warped.png      # 透视校正后的图（--warp）
├── elements/
│   ├── element_01.png     # 分离的每个 UI 元素
│   ├── element_02.png
│   └── ...
├── metadata.json          # 元素元数据（bbox、面积、坐标等）
├── summary.txt            # 处理摘要
└── debug/                 # --debug 模式输出
    ├── mask_overlay.png
    ├── foreground_mask.png
    ├── corners.png
    └── elements_annotated.png
```

## 注意事项

- **自动检测**：默认 `--bg-type auto` 会尝试从四角像素和棋盘格角点检测背景类型
- **chroma 算法**：直接复制自 spritesheet skill 的 `chroma.py`（2026-06-16），保持与视频抠图结果一致
- **棋盘格检测**：优先用 `findChessboardCornersSB`（OpenCV 4.5+），失败时回退到基于局部方差的纹理检测
- **透视校正**：要求棋盘格完整可见，否则跳过并警告
- **元素分离**：基于 `findContours(RETR_EXTERNAL)`，按面积/长宽比过滤，小于 `--min-element-area` 的元素会被丢弃

## 背景色建议

| 背景类型 | 色值 | 抠图效果 |
|----------|------|----------|
| 🟢 绿幕（推荐） | `#00FF00` / `RGB(0,255,0)` | ✅ 最佳 |
| 🔵 蓝幕 | `#0000FF` / `RGB(0,0,255)` | ✅ 好 |
| ⚪ 白幕 | `#FFFFFF` | ⚠️ 边缘易混淆 |
| ⚫ 黑幕 | `#000000` | ⚠️ 暗部易丢失 |
| 🔲 棋盘格 | 灰白/深灰相间 | ✅ 几何精确 |

## 算法细节

详细的算法流程和参数说明见 [references/pipeline.md](references/pipeline.md)。
