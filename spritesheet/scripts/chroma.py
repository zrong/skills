#!/usr/bin/env python3
"""Chroma Key 抠图：HSV 色相分割，支持绿幕/蓝幕/白幕/黑幕自动检测。

从主文件 spritesheet.py 抽出，供主管线与 loopdetect（循环检测阶段抠图）共用，
避免主入口作为 __main__ 时反向 import 的双重加载问题。
"""

import cv2
import numpy as np

# ─── 背景色 HSV 范围 ───────────────────────────────────────────────

BG_HSV_RANGES = {
    "green": [
        # 标准绿幕：高饱和度 + 中高亮度（排除军绿等低饱和度绿色主体）
        {"h": (30, 90), "s": (80, 255), "v": (80, 255)},
        # 亮绿幕（灯光过曝）：饱和度稍低但亮度很高
        {"h": (30, 90), "s": (50, 255), "v": (180, 255)},
    ],
    "blue": [
        {"h": (85, 135), "s": (80, 255), "v": (80, 255)},
        {"h": (85, 135), "s": (50, 255), "v": (180, 255)},
    ],
    "white": [
        {"h": (0, 180), "s": (0, 30), "v": (200, 255)},
    ],
    "black": [
        {"h": (0, 180), "s": (0, 255), "v": (0, 50)},
    ],
}


# ─── 自动背景色检测 ────────────────────────────────────────────────

def detect_bg_color(frame: np.ndarray, sample_ratio: float = 0.05) -> str:
    """采样视频四角像素，统计 HSV 分布判断背景色。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    margin = int(min(h, w) * sample_ratio)

    # 采样四个角
    corners = [
        hsv[0:margin, 0:margin],
        hsv[0:margin, w - margin:w],
        hsv[h - margin:h, 0:margin],
        hsv[h - margin:h, w - margin:w],
    ]
    samples = np.vstack([c.reshape(-1, 3) for c in corners])

    mean_h = np.mean(samples[:, 0])
    mean_s = np.mean(samples[:, 1])
    mean_v = np.mean(samples[:, 2])

    # 判断逻辑
    if mean_s < 30 and mean_v > 200:
        return "white"
    if mean_v < 50:
        return "black"
    if 35 <= mean_h <= 85 and mean_s > 40:
        return "green"
    if 85 < mean_h <= 135 and mean_s > 40:
        return "blue"

    # 默认按白色处理
    print(f"  背景色无法自动判断 (H={mean_h:.0f}, S={mean_s:.0f}, V={mean_v:.0f})，按白色处理")
    return "white"


# ─── Chroma Key 抠图 ───────────────────────────────────────────────

def remove_bg_chroma(frame: np.ndarray, bg_color: str = "auto") -> np.ndarray:
    """HSV 色相分割抠图，返回带 alpha 通道的 BGRA 图像。"""
    if bg_color == "auto":
        bg_color = detect_bg_color(frame)
        print(f"  自动检测背景色: {bg_color}")

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    ranges = BG_HSV_RANGES.get(bg_color, BG_HSV_RANGES["white"])

    # 合并所有 HSV 范围的 mask
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for r in ranges:
        lower = np.array([r["h"][0], r["s"][0], r["v"][0]])
        upper = np.array([r["h"][1], r["s"][1], r["v"][1]])
        partial = cv2.inRange(hsv, lower, upper)
        mask = cv2.bitwise_or(mask, partial)

    # ── 边缘清理：先用大核把碎片/噪点抹掉，再小核细化 ──
    big_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, big_kernel, iterations=1)
    small_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, small_kernel, iterations=2)

    # 反转：背景=0（透明），前景=255
    mask = cv2.bitwise_not(mask)

    # 边缘羽化（消除锯齿和绿边）
    mask = cv2.GaussianBlur(mask, (3, 3), 0)

    bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)

    # ── 绿幕 despill：覆盖所有 alpha>0 像素，压制残留绿色 ──
    # 策略：
    #   - 主体内部（mask >= 240）：衣领/裆部等暗褶皱里 G 可能比 R 高 5-30
    #     → 用 (g_i > b_i + 5) & (g_i > r_i + 5) 双向条件，仅压制真正偏绿像素
    #   - 主体边缘（mask < 240）：过渡区 G 普遍偏高
    #     → 放宽到 (g_i > b_i + 1) | (g_i > r_i + 1)
    # 目标 G = max(B,R) 与 (B+R)/2 中较大者，避免压出死黑
    if bg_color == "green":
        b, g, r = cv2.split(bgra[:, :, :3])
        g_i = g.astype(int)
        b_i = b.astype(int)
        r_i = r.astype(int)
        # 主体内部（核心 240-255）：严格双向条件
        spill_core = (g_i > b_i + 5) & (g_i > r_i + 5) & (mask >= 240)
        # 主体边缘（过渡 0-239）：宽松单向条件
        spill_edge = ((g_i > b_i + 1) | (g_i > r_i + 1)) & (mask > 0) & (mask < 240)
        spill = spill_core | spill_edge
        if spill.any():
            target_g = np.maximum(np.maximum(b_i, r_i), (b_i + r_i) // 2)
            new_g = np.where(spill, target_g, g_i).astype(np.uint8)
            bgra[:, :, 1] = new_g

    bgra[:, :, 3] = mask
    return bgra
