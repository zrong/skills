#!/usr/bin/env python3
"""主体分析工具：bbox + alpha 加权质心 + mask 内 MSE 向量化计算。

供 spritesheet 主管线（裁切框计算）和 loopdetect（循环检测）共用。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class SubjectInfo:
    """单帧主体信息。"""

    x: int  # bbox 左上 x
    y: int  # bbox 左上 y
    w: int  # bbox 宽
    h: int  # bbox 高
    cx: float  # alpha 加权质心 x
    cy: float  # alpha 加权质心 y
    area: int  # 主体像素数（alpha 加权质量 / 255）
    height_span: int  # 主体竖直跨度（= h，独立字段便于扩展）


def detect_subject_info(alpha_channel: np.ndarray) -> SubjectInfo:
    """检测主体边界框 + alpha 加权质心。

    cv2.moments 直接吃 uint8 alpha 当灰度图，像素值即权重，天然 alpha 加权。
    alpha 全 0 时返回整图、质心=图像中心、area=0。
    """
    H, W = alpha_channel.shape[:2]
    coords = cv2.findNonZero(alpha_channel)
    if coords is None:
        return SubjectInfo(0, 0, W, H, W / 2.0, H / 2.0, 0, H)

    x, y, w, h = cv2.boundingRect(coords)
    m = cv2.moments(alpha_channel)
    m00 = m["m00"] or 1.0
    cx = m["m10"] / m00
    cy = m["m01"] / m00
    area = int(round(m["m00"] / 255.0))
    return SubjectInfo(int(x), int(y), int(w), int(h), float(cx), float(cy), area, int(h))


def detect_subject_bbox(alpha_channel: np.ndarray) -> tuple[int, int, int, int]:
    """[保留兼容] 检测主体边界框 (x, y, w, h)。"""
    info = detect_subject_info(alpha_channel)
    return info.x, info.y, info.w, info.h


def subject_mask_grays(
    frames_bgra: list[np.ndarray], alpha_threshold: int = 16
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """对一批 BGRA 帧，返回 (masks, subject_grays)。

    - masks: 二值 mask（uint8 0/255），alpha > threshold 的像素
    - subject_grays: 灰度图（float32），背景像素置 0（仅主体区域有值）
      背景置 0 是 mask 内 MSE 向量化的前提：两帧背景像素差恒为 0，不污染差异。
    """
    masks: list[np.ndarray] = []
    grays: list[np.ndarray] = []
    for bgra in frames_bgra:
        alpha = bgra[:, :, 3]
        mask = (alpha > alpha_threshold).astype(np.uint8) * 255
        gray = cv2.cvtColor(bgra[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
        masks.append(mask)
        grays.append(gray * (mask > 0))
    return masks, grays


def mask_mse_matrix(subject_grays: list[np.ndarray], union_mask: np.ndarray) -> np.ndarray:
    """计算 mask 内的全局帧对 MSE 矩阵（向量化）。

    输入 subject_grays 已将背景置 0。返回 (M, M) 矩阵，归一化到 [0, 1]：
    D[i,j] = Σ(g_i - g_j)² 仅在主体区域累积（背景为 0 不贡献），除以并集 mask
    面积 × 255²。

    原理：背景置 0 后 ||G[i]-G[j]||² 只统计主体像素，用一次 G@G.T 算出全矩阵，
    比双层 Python 循环快约 100×。分母用固定 union_mask（所有帧 mask 并集）换性能，
    对主体面积变化大的视频有常数级偏差，不影响相对排序。
    """
    G = np.stack([g.ravel() for g in subject_grays])  # (M, P)
    sq = (G * G).sum(axis=1)  # (M,)
    D = sq[:, None] + sq[None, :] - 2.0 * (G @ G.T)  # (M, M) 像素差平方和
    D = np.maximum(D, 0.0)
    area = float(union_mask.sum()) + 1e-6
    mse = D / (area * (255.0 ** 2))
    np.fill_diagonal(mse, 0.0)
    return mse


def centroid_and_height(masks: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """对一批 mask，返回 (centroids (M,2), heights (M,))。

    质心用 cv2.moments（二值 mask）；高度 = mask 竖直跨度。
    全 0 mask 的质心记为 nan（评分时跳过）。
    """
    centroids = np.full((len(masks), 2), np.nan, dtype=np.float64)
    heights = np.zeros(len(masks), dtype=np.int32)
    for i, mask in enumerate(masks):
        m = cv2.moments(mask)
        if m["m00"] > 0:
            centroids[i, 0] = m["m10"] / m["m00"]
            centroids[i, 1] = m["m01"] / m["m00"]
        ys, _ = np.where(mask > 0)
        if len(ys):
            heights[i] = int(ys.max() - ys.min())
    return centroids, heights
