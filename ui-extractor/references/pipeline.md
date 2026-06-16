# UI Extractor — 算法流程文档

本文档详细描述 UI Extractor 的算法实现、参数选择和性能调优建议。

## 整体流水线

```
输入图片 (BGR)
    │
    ▼
[1] 背景类型检测 (detect_bg_type)
    │
    ├──► "chroma" ──► [2a] Chroma Key 抠图
    │                       │
    │                       ▼
    └──► "checkerboard" ─► [2b] 棋盘格检测 + 凸包 mask
                                │
                                ▼
[3] Mask 后处理 (形态学清理 + 羽化)
    │
    ▼
[4] 输出 BGRA 透明 PNG
    │
    ▼
[5] (可选) 透视校正 (--warp)
    │
    ▼
[6] (可选) 元素分离
    ├── findContours(RETR_EXTERNAL)
    ├── 面积 / 长宽比 / 圆形度过滤
    └── 裁剪 + 保存为单独 PNG
```

---

## 阶段 1: 背景类型检测

**函数**：`detect_bg_type(image) -> str`

**策略**：
1. 采样四角的 5% 像素，计算 RGB 标准差
2. 若 `std > 8` → 判定为棋盘格（角落纹理复杂）
3. 否则调用 `detect_checkerboard` 检测角点；若检测到角点 → 棋盘格
4. 否则调用 `detect_bg_color` 检测关键色 → chroma
5. 都失败 → `unknown`（抛错）

**调优建议**：
- 棋盘格角落 std 阈值默认 8，可根据具体场景调高（噪声大时）或调低（软渐变时）
- 软渐变棋盘格可能角落均匀，需要依赖 `detect_checkerboard` 的角点检测

---

## 阶段 2a: Chroma Key 抠图

**函数**：`remove_chroma_bg(image, bg_color) -> (bgra, mask)`

**实现**（直接复制自 `spritesheet/scripts/chroma.py`）：

1. **HSV 转换**：`cv2.cvtColor(BGR -> HSV)`
2. **多范围 inRange**：使用 `BG_HSV_RANGES` 字典中预定义的范围
   - 绿幕：2 个范围（标准 + 亮绿）
   - 蓝幕：2 个范围（标准 + 亮蓝）
   - 白幕：1 个范围
   - 黑幕：1 个范围
3. **形态学清理**：
   - 大核（7×7）开运算 → 去除小噪点
   - 小核（3×3）闭运算（2 次）→ 填充前景小洞
4. **反转**：背景=0（透明），前景=255
5. **羽化**：`GaussianBlur(3×3)` → 消除锯齿
6. **绿幕 despill**（仅 green 模式）：
   - 主体内部（mask >= 240）：`g > b+5 & g > r+5`
   - 主体边缘（mask < 240）：`g > b+1 | g > r+1`
   - 目标 G = `max(B, R, (B+R)/2)`，避免压出死黑

**HSV 范围表**：

| 颜色 | H 范围 | S 范围 | V 范围 | 备注 |
|------|--------|--------|--------|------|
| 绿色-标准 | 30-90 | 80-255 | 80-255 | 排除低饱和度军绿 |
| 绿色-亮 | 30-90 | 50-255 | 180-255 | 灯光过曝场景 |
| 蓝色-标准 | 85-135 | 80-255 | 80-255 | |
| 蓝色-亮 | 85-135 | 50-255 | 180-255 | |
| 白色 | 0-180 | 0-30 | 200-255 | |
| 黑色 | 0-180 | 0-255 | 0-50 | |

---

## 阶段 2b: 棋盘格背景去除

**函数**：`remove_checkerboard_bg(image, corners=None, pattern_size) -> (bgra, mask)`

**步骤**：

1. **角点检测**（若 corners 未提供）：
   - 优先 `findChessboardCornersSB`（OpenCV 4.5+，稀疏式，更鲁棒）
   - 回退 `findChessboardCorners`（经典 Harris 式）
   - 二次回退 `_detect_by_texture`（基于 FFT 估算网格周期）

2. **凸包 mask**：
   - `convexHull(corners)` → 棋盘格外边界
   - `fillConvexPoly` → 填充实心 mask
   - `erode(3×3, 1)` → 收缩 1 像素，保留前景边界

3. **失败回退**（corners 全失败时）：
   - 基于局部方差（15×15 窗口）
   - 棋盘格方差高，前景方差低
   - Otsu 反阈值 → 前景 mask

4. **Mask 清理**：
   - `morphologyEx(MORPH_OPEN, 7×7)` → 去小噪点
   - `morphologyEx(MORPH_CLOSE, 3×3, 2次)` → 填小洞
   - `GaussianBlur(3×3)` → 软边缘

---

## 阶段 4: BGRA 输出

**实现**：
- `cvtColor(BGR -> BGRA)` → 4 通道
- `bgra[:, :, 3] = mask` → 设置 alpha 通道
- 背景像素 alpha=0（完全透明）
- 前景像素 alpha=255（完全不透明）
- 边缘羽化后产生半透明过渡

---

## 阶段 5: 透视校正（--warp）

**函数**：`warp_perspective(image, corners, pattern_size) -> warped`

**步骤**：
1. 把 `corners` 重塑为 `(rows, cols, 2)` 网格
2. 取 4 个外角点（grid[0,0]、grid[0,-1]、grid[-1,-1]、grid[-1,0]）
3. 计算目标尺寸 = 4 边最大长度
4. `getPerspectiveTransform(src_pts, dst_pts)` → 变换矩阵
5. `warpPerspective(image, M, (w, h), borderValue=(255,255,255))`

**限制**：
- 棋盘格必须完整可见（4 角都在图像内）
- 棋盘格检测失败时跳过并警告

---

## 阶段 6: 元素分离

**函数**：`extract_ui_elements(mask, original, min_area=500, max_aspect=10) -> List[Element]`

**步骤**：
1. 二值化：`mask > 127 → 255`
2. `findContours(RETR_EXTERNAL)` → 仅最外层轮廓
3. **过滤策略**：
   - `area < min_area` → 丢弃
   - `aspect_ratio > max_aspect` → 丢弃（噪声细条）
4. **排序**：从左到右，从上到下
5. **裁剪**：
   - `boundingRect` → x, y, w, h
   - 加 2 像素 padding
   - 裁剪 BGRA（alpha 来自 mask）
6. **元数据**：bbox、area、aspect_ratio、circularity（4π·area/perimeter²）

**RETRIEVAL 模式选择**：
- `RETR_EXTERNAL` ✅ 仅外轮廓 → 元素分离（避免内孔洞干扰）
- `RETR_TREE` → 嵌套结构（按钮内的图标）

---

## 性能特性

| 阶段 | 典型耗时 (1920x1080) | 内存 |
|------|---------------------|------|
| 背景类型检测 | < 100ms | 低 |
| Chroma 抠图 | 50-200ms | 低 |
| 棋盘格检测 (SB) | 100-500ms | 中 |
| 棋盘格检测 (回退) | 50-200ms | 低 |
| Mask 清理 + 羽化 | 20-50ms | 低 |
| 透视校正 | 50-100ms | 中 |
| 元素分离 | 100-500ms | 中 |

总计：单张图片 < 2 秒

---

## 调优建议

### 边缘残留背景色
- 当前实现使用固定 HSV 范围（来自 spritesheet），无 tolerance 参数
- 调整 `BG_HSV_RANGES` 字典可针对特定场景

### 软渐变棋盘格检测失败
- 检查 `detect_checkerboard` 的 confidence
- 调整 `pattern_size`（默认 9x6 内角点 = 10x7 方格）
- 手动指定 `--bg-type checkerboard` 跳过 auto 检测

### 元素粘连
- 减小 `--min-element-area` 让更多元素进入候选
- 增大 `extract_elements.py` 中的 `MORPH_ERODE` 迭代次数（在源码中调整）

### 元素过小被误删
- 减小 `--min-element-area`（默认 500）
- 检查 `contour_aspect_ratio` 过滤是否过严

### 元素过多噪声
- 增大 `--min-element-area`
- 添加形态学闭运算（在 `extract_elements` 中）

---

## 与 spritesheet skill 的差异

| 维度 | spritesheet | ui-extractor |
|------|-------------|--------------|
| 输入 | 视频（多帧） | 静态图片（单帧） |
| 背景类型 | 绿/蓝/白/黑/auto | + 棋盘格 |
| 循环检测 | ✅ (--smart) | ❌ |
| 主体检测 | ✅ (--analyze) | ❌ |
| 透视校正 | ❌ | ✅ (--warp) |
| 元素分离 | ❌ | ✅ |
| 输出 | 序列帧 + spritesheet + player | BGRA + 元素 PNG |

**算法复用**：
- `BG_HSV_RANGES`（HSV 范围表）：直接复制
- `detect_bg_color`（自动背景色）：直接复制
- `remove_bg_chroma`（chroma 抠图核心）：直接复制
- 来源标注：`remove_bg.py` 文件头注释 "Copied from spritesheet skill, 2026-06-16"
- 同步策略：当 spritesheet 的 chroma 算法更新时，需手动同步

---

## 已知限制

1. **复杂嵌套元素**：当前用 `RETR_EXTERNAL`，无法分离嵌套结构（如按钮里的图标）
2. **半透明前景**：chroma 抠图对半透明区域（如玻璃）效果不佳
3. **类棋盘格纹理**：织物、栅栏等周期性纹理可能被误判为棋盘格
4. **彩色棋盘格**：仅支持灰白系（标准 Photoshop 透明背景）
5. **chroma 模式无 tolerance 参数**：固定使用预定义 HSV 范围
6. **灰度 UI 元素在棋盘格场景下**：当前棋盘格用 HSV 饱和度检测前景。灰度 UI 元素（黑色按钮、灰色文本条、白星等）和棋盘格（灰白）有相似的低饱和度，**会被错误地识别为背景**。建议场景：UI 元素为彩色时使用棋盘格模式；灰度 UI 元素建议使用 `--bg-type chroma --bg-color white`
