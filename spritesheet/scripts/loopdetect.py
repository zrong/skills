#!/usr/bin/env python3
"""循环检测：主体感知全局接缝检测（默认）+ 旧 CV 帧差法（from-frame-zero/回退）。

detect_loop 按优先级调度：
    manual（--loop-start/--loop-end）> from-frame-zero（--from-frame-zero）> global（默认）

find_loop_point_global 是默认，突破旧 find_loop_point_cv 两个缺陷：
  1. 不假设循环从第 0 帧开始——全局扫描所有 (start,end) 对
  2. MSE 在主体 mask 内计算——排除背景稀释造成的"虚低"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from chroma import remove_bg_chroma, detect_bg_color
from subject import subject_mask_grays, mask_mse_matrix, centroid_and_height


# ─── 全局接缝检测参数 ──────────────────────────────────────────────

@dataclass
class GlobalLoopConfig:
    """主体感知全局接缝检测的参数。"""

    max_analysis_frames: int = 180  # 分析用帧数上限（控 N² 成本）
    alpha_threshold: int = 16  # alpha > 该值视为主体
    min_subject_ratio: float = 0.05  # mask 占比低于此值视为主体分离失败 → 回退
    min_cycle_ratio: float = 0.12  # 最短周期占分析帧数比例
    min_cycle_abs: int = 4  # 最短周期绝对帧数
    max_cycle_ratio: float = 1.0  # 最长周期比例
    candidate_quantile: float = 0.30  # 端点 MSE 粗筛分位
    w_endpoint: float = 1.0  # 端点相似度权重
    w_richness: float = 0.6  # 运动丰富度权重
    w_centroid: float = 0.4  # 质心稳定性权重
    w_translation: float = 0.3  # 平移型循环 bonus 权重
    translation_resid_thresh: float = 2.0  # 质心线性拟合残差阈值（像素），小于此视为平移型
    min_endpoint_similarity: float = 0.5  # 最优候选端点相似度低于此值 → 回退
    seam_warn_mse: float = 0.015  # 首尾衔接 MSE 警告阈值


# ─── 主体感知全局接缝检测（默认）──────────────────────────────────

def find_loop_point_global(
    video_path: Path,
    config: "GlobalLoopConfig | None" = None,
    *,
    return_report: bool = False,
    bg_color: str = "auto",
):
    """主体感知全局接缝检测。返回 (loop_start, loop_end) 原始帧索引。

    return_report=True 时额外返回诊断 dict（供 --analyze 复用，省一次 chroma）。
    """
    if config is None:
        config = GlobalLoopConfig()

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0

    if total < 4:
        cap.release()
        return _fallback_to_cv(video_path, return_report, "视频过短")

    # ── 抽分析密集帧（自适应 stride，控 N² 成本）──
    n_target = min(total, config.max_analysis_frames)
    stride = max(1, round(total / n_target))
    analysis_indices = list(range(0, total, stride))

    frames_bgr = []
    for idx in analysis_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            frames_bgr.append(frame)
    cap.release()

    analysis_indices = analysis_indices[: len(frames_bgr)]
    M = len(frames_bgr)

    if M < 4:
        return _fallback_to_cv(video_path, return_report, "可读帧过少")

    print(f"  全局检测: {total} 帧 → 分析 {M} 帧 (stride={stride})")

    # ── chroma key（背景色只检测一次，避免逐帧噪音）──
    if bg_color == "auto":
        bg_color = detect_bg_color(frames_bgr[0])
        print(f"  全局检测背景色: {bg_color}")
    frames_bgra = [remove_bg_chroma(f, bg_color) for f in frames_bgr]

    masks, subject_grays = subject_mask_grays(frames_bgra, config.alpha_threshold)

    union_mask = np.any(np.stack(masks, 0), axis=0)  # bool
    mask_ratio = float(union_mask.sum()) / float(union_mask.size)
    if mask_ratio < config.min_subject_ratio:
        print(f"  ⚠ 主体分离不足 (mask 占比 {mask_ratio:.3f})，回退全画面法")
        return _fallback_to_cv(video_path, return_report, f"主体分离不足({mask_ratio:.3f})")

    # ── mask 内全局 MSE 矩阵（向量化 G@G.T）──
    mse_matrix = mask_mse_matrix(subject_grays, union_mask)
    centroids, heights = centroid_and_height(masks)

    # ── 候选生成 + 粗筛 ──
    min_len = max(int(M * config.min_cycle_ratio), config.min_cycle_abs)
    max_len = max(min_len + 1, int(M * config.max_cycle_ratio))

    pairs = []
    for a in range(M):
        for b in range(a + min_len, min(M, a + max_len + 1)):
            pairs.append((a, b))

    if not pairs:
        return _fallback_to_cv(video_path, return_report, "无合法周期区间")

    ep = np.array([mse_matrix[a, b] for a, b in pairs])
    thresh = np.quantile(ep, config.candidate_quantile)
    cand_pairs = [(a, b) for (a, b), e in zip(pairs, ep) if e <= thresh]

    # richness 归一化尺度：所有相邻帧 MSE 的 90 分位
    if M >= 2:
        adj = np.array([mse_matrix[k, k + 1] for k in range(M - 1)])
        richness_scale = max(float(np.quantile(adj, 0.9)), 1e-6)
    else:
        richness_scale = 1e-6

    # ── 三因子评分 + 平移 bonus ──
    scored = []
    for a, b in cand_pairs:
        sim_endpoint = 1.0 - float(mse_matrix[a, b])

        if b > a:
            seg = np.array([mse_matrix[k, k + 1] for k in range(a, b)])
            richness = float(seg.mean()) if len(seg) else 0.0
        else:
            richness = 0.0
        richness_norm = min(richness / richness_scale, 1.0)

        cx_seg = centroids[a : b + 1, 0]
        cy_seg = centroids[a : b + 1, 1]
        cstd = float(np.nanstd(cx_seg)) + float(np.nanstd(cy_seg))
        cent_stability = 1.0 / (1.0 + cstd)

        tbonus = _translation_bonus(cx_seg, config)

        score = (
            config.w_endpoint * sim_endpoint
            + config.w_richness * richness_norm
            + config.w_centroid * cent_stability
            + config.w_translation * tbonus
        )
        scored.append(
            {
                "loop_start": int(analysis_indices[a]),
                "loop_end": int(analysis_indices[b]),
                "score": float(score),
                "endpoint_similarity": float(sim_endpoint),
                "richness": float(richness),
                "centroid_stability": float(cent_stability),
                "seam_mse": float(mse_matrix[a, b]),
            }
        )

    scored.sort(key=lambda c: c["score"], reverse=True)
    best = scored[0]

    # ── 端点相似度过低 → 回退 ──
    if best["endpoint_similarity"] < config.min_endpoint_similarity:
        print(f"  ⚠ 最优候选端点相似度过低 ({best['endpoint_similarity']:.3f})，回退全画面法")
        return _fallback_to_cv(video_path, return_report, "端点相似度过低")

    loop_start, loop_end = best["loop_start"], best["loop_end"]

    # ── 首尾衔接验证（警告，不阻断）──
    seam_warning = None
    if best["seam_mse"] > config.seam_warn_mse:
        seam_warning = f"首尾衔接差异较大 (主体 MSE={best['seam_mse']:.4f} > {config.seam_warn_mse})"
        print(f"  ⚠ {seam_warning}，建议用 --analyze 复核或手动指定 --loop-start/--loop-end")

    print(
        f"  全局循环检测: 起点={loop_start}, 终点={loop_end} "
        f"(相似度={best['endpoint_similarity']:.3f}, 丰富度={best['richness']:.4f}, "
        f"质心稳定={best['centroid_stability']:.3f}, 候选={len(scored)})"
    )

    if return_report:
        report = {
            "meta": {
                "total_frames": total,
                "fps": float(fps),
                "analysis_frames": M,
                "stride": stride,
                "bg_color": bg_color,
                "mask_ratio": mask_ratio,
            },
            "mse": {
                "vs_frame0": mse_matrix[0, :].tolist(),
                "pairwise_summary": {
                    "min": float(mse_matrix.min()),
                    "median": float(np.median(mse_matrix)),
                    "p90": float(np.quantile(mse_matrix, 0.9)),
                    "matrix_shape": list(mse_matrix.shape),
                },
            },
            "centroid_trajectory": {
                "cx": np.nan_to_num(centroids[:, 0]).tolist(),
                "cy": np.nan_to_num(centroids[:, 1]).tolist(),
            },
            "subject_size": {"height": heights.tolist()},
            "period_candidates": scored[:10],
            "recommendation": {
                "loop_start": loop_start,
                "loop_end": loop_end,
                "method": "global",
                "seam_warning": seam_warning,
            },
        }
        return loop_start, loop_end, report
    return loop_start, loop_end


def _translation_bonus(cx_seg: np.ndarray, config: "GlobalLoopConfig") -> float:
    """检测区间内质心 x 是否近线性（平移型循环），是则返回 1.0 作为 bonus。

    平移型循环的质心 std 天然偏大，但若 cx 近线性变化（polyfit 残差小），说明
    是稳定平移而非抖动，不应因质心稳定性扣分，故给 bonus 抵消。
    """
    valid = ~np.isnan(cx_seg)
    if valid.sum() < 3:
        return 0.0
    xs = np.where(valid)[0].astype(np.float64)
    cxs = cx_seg[valid].astype(np.float64)
    try:
        p = np.polyfit(xs, cxs, 1)
        resid = float(np.sqrt(np.mean((cxs - np.polyval(p, xs)) ** 2)))
    except Exception:
        return 0.0
    return 1.0 if resid < config.translation_resid_thresh else 0.0


def _fallback_to_cv(video_path: Path, return_report: bool, reason: str = ""):
    """回退到全画面 CV 帧差法（find_loop_point_cv）。"""
    if reason:
        print(f"  回退全画面法: {reason}")
    ls, le = find_loop_point_cv(video_path)
    if return_report:
        report = {
            "fallback": "cv",
            "reason": reason,
            "recommendation": {
                "loop_start": ls,
                "loop_end": le,
                "method": "cv_fallback",
                "seam_warning": None,
            },
        }
        return ls, le, report
    return ls, le


# ─── 旧 CV 帧差法（--from-frame-zero / 回退用）─────────────────────

def _compute_frame_mse_series(video_path: Path) -> tuple[list[np.ndarray], np.ndarray]:
    """读取所有帧，返回 (grayscale_frames, mse_vs_frame0) 数组。"""
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    grays = []
    for _ in range(total):
        ret, frame = cap.read()
        if not ret:
            break
        grays.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32))
    cap.release()

    if not grays:
        return grays, np.array([])

    ref = grays[0]
    mse_series = np.array([np.mean((ref - g) ** 2) / (255 ** 2) for g in grays])
    return grays, mse_series


def find_loop_point_cv(video_path: Path) -> tuple[int, int]:
    """[旧] 从第 0 帧起锚的 CV 帧差法循环检测。

    局限：硬编码 start=0（假设循环从首帧开始）；MSE 全画面灰度（背景稀释→虚低）。
    保留为 --from-frame-zero 与全局检测失败时的回退。
    """
    grays, mse_series = _compute_frame_mse_series(video_path)
    total = len(grays)

    if total < 4:
        return 0, total - 1

    fps = cv2.VideoCapture(str(video_path)).get(cv2.CAP_PROP_FPS)
    min_cycle = max(int(fps * 0.3), total // 7, 4)

    frame_diffs = np.array(
        [np.mean((grays[i] - grays[i + 1]) ** 2) / (255 ** 2) for i in range(total - 1)]
    )

    candidates = []
    for i in range(min_cycle, total):
        window = max(3, min_cycle // 4)
        lo = max(0, i - window)
        hi = min(total, i + window + 1)
        if mse_series[i] == np.min(mse_series[lo:hi]):
            richness = float(np.sum(frame_diffs[0:i]))
            similarity = 1.0 - mse_series[i]
            score = richness * (0.5 + 0.5 * similarity)
            candidates.append(
                {"start": 0, "end": i, "richness": richness, "similarity": similarity, "score": score}
            )

    if not candidates:
        print(f"  CV 循环检测: 未找到明确周期，使用整段视频 (0-{total - 1})")
        return 0, total - 1

    best = max(candidates, key=lambda c: c["score"])
    print(
        f"  CV 循环检测: 起点={best['start']}, 终点={best['end']} "
        f"(丰富度={best['richness']:.5f}, 相似度={best['similarity']:.3f}, "
        f"候选数={len(candidates)})"
    )
    return best["start"], best["end"]


# ─── 调度 ──────────────────────────────────────────────────────────

def detect_loop(
    video_path: Path,
    *,
    mode: str = "global",
    manual: "tuple[int, int] | None" = None,
    bg_color: str = "auto",
) -> tuple[int, int, str]:
    """按优先级返回 (loop_start, loop_end, method)。

    mode: 'manual' | 'from_frame_zero' | 'global'
    manual: (start, end) 或 None（mode='manual' 时必填）
    method 取值: manual | cv_from_zero | global
    """
    if mode == "manual":
        assert manual is not None, "mode='manual' 需提供 manual=(start,end)"
        return manual[0], manual[1], "manual"
    if mode == "from_frame_zero":
        ls, le = find_loop_point_cv(video_path)
        return ls, le, "cv_from_zero"
    ls, le = find_loop_point_global(video_path, bg_color=bg_color)
    return ls, le, "global"
