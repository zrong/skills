#!/usr/bin/env python3
"""--analyze 诊断模式：输出循环分析报告，不生成 spritesheet。

复用 find_loop_point_global(return_report=True) 的内部产物（MSE 矩阵摘要、质心轨迹、
候选评分），再补充帧差自相关（周期检测）与主体大小趋势（U 型波动 vs 持续放大），
写 <video>.analysis.json。自动检测不可靠时，供 Agent/用户据报告手动指定
--loop-start/--loop-end。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from loopdetect import GlobalLoopConfig, find_loop_point_global


def _autocorr(signal: np.ndarray) -> np.ndarray:
    """归一化自相关函数（lag 0..n-1）。"""
    n = len(signal)
    if n < 4:
        return np.array([])
    signal = signal - signal.mean()
    denom = np.dot(signal, signal)
    if denom <= 0:
        return np.array([])
    full = np.correlate(signal, signal, mode="full")[n - 1:]
    return full / denom


def find_period(signal: np.ndarray, min_lag: int = 2) -> list[dict]:
    """在自相关曲线上找局部极大峰（候选周期）。"""
    acf = _autocorr(signal)
    peaks = []
    for i in range(min_lag, len(acf) - 1):
        if acf[i] > acf[i - 1] and acf[i] > acf[i + 1] and acf[i] > 0.2:
            peaks.append({"lag": int(i), "acf": float(acf[i])})
    peaks.sort(key=lambda p: -p["acf"])
    return peaks


def analyze_size_trend(heights: np.ndarray) -> tuple[str, float, float, float]:
    """对主体高度序列做线性/二次拟合，判断趋势。

    返回 (trend, slope, resid_linear, resid_quadratic)：
      trend ∈ {monotonic_grow, monotonic_shrink, u_shape, stable}
    """
    heights = np.asarray(heights, dtype=np.float64)
    n = len(heights)
    if n < 4 or heights.std() < 1e-6:
        return "stable", 0.0, 0.0, 0.0
    x = np.arange(n, dtype=np.float64)
    plin = np.polyfit(x, heights, 1)
    pquad = np.polyfit(x, heights, 2)
    resid_lin = float(np.sqrt(np.mean((heights - np.polyval(plin, x)) ** 2)))
    resid_quad = float(np.sqrt(np.mean((heights - np.polyval(pquad, x)) ** 2)))
    slope = float(plin[0])  # 每帧高度变化（像素）
    # 二次拟合显著优于线性 → 非单调（U 型 / 倒 U）
    if resid_quad < resid_lin * 0.7:
        return "u_shape", slope, resid_lin, resid_quad
    # 斜率显著（每帧 > 0.3 像素）→ 单调
    if abs(slope) > 0.3:
        return "monotonic_grow" if slope > 0 else "monotonic_shrink", slope, resid_lin, resid_quad
    return "stable", slope, resid_lin, resid_quad


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(values, width: int = 48) -> str:
    """把数值序列渲染成一行 ASCII 柱状图。"""
    vals = [float(v) for v in values if v == v]  # 去 nan
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    rng = hi - lo or 1.0
    n = len(vals)
    step = max(1, n // width)
    sampled = vals[::step][:width]
    return "".join(_SPARK[min(7, int((v - lo) / rng * 7.999))] for v in sampled)


def print_ascii_summary(report: dict) -> None:
    """打印文本摘要（vs帧0 MSE 曲线、质心轨迹、top 候选、推荐）。"""
    print("\n──── 分析摘要 ────")
    vs0 = report.get("mse", {}).get("vs_frame0", [])
    if vs0:
        print(f"vs帧0 MSE:   {_sparkline(vs0)}")
    cx = report.get("centroid_trajectory", {}).get("cx", [])
    if cx:
        print(f"质心 cx:     {_sparkline(cx)}")
    h = report.get("subject_size", {}).get("height", [])
    if h:
        print(f"主体高度:    {_sparkline(h)}  (趋势={report.get('subject_size', {}).get('trend', '?')})")
    ac = report.get("autocorr", {})
    if ac.get("period_frames") is not None:
        print(f"自相关周期:  ~{ac['period_frames']} 帧 (置信度={ac.get('confidence', 0):.2f})")
    cands = report.get("period_candidates", [])
    if cands:
        print("Top 候选:")
        for c in cands[:5]:
            print(
                f"  [{c['loop_start']:>3}-{c['loop_end']:<3}] "
                f"score={c['score']:.3f} 相似={c['endpoint_similarity']:.3f} "
                f"丰富={c['richness']:.4f} 接缝={c['seam_mse']:.4f}"
            )
    rec = report.get("recommendation", {})
    print(f"推荐: {rec.get('loop_start')}-{rec.get('loop_end')} ({rec.get('method')})")
    if rec.get("seam_warning"):
        print(f"  ⚠ {rec['seam_warning']}")
    print("──── 摘要结束 ────\n")


def run_analyze(
    video_path: Path,
    config: "GlobalLoopConfig | None" = None,
    *,
    txt: bool = False,
    bg_color: str = "auto",
) -> Path:
    """生成 <video>.analysis.json（可选打印 ASCII 摘要）。"""
    if config is None:
        config = GlobalLoopConfig()

    ls, le, report = find_loop_point_global(video_path, config, return_report=True, bg_color=bg_color)

    # 全局检测回退到 cv 时，report 只有基础字段，无法 enrich
    if report.get("fallback"):
        print(f"  全局检测已回退（{report.get('reason', '?')}），报告仅含基础信息")
    else:
        # 帧差自相关（用 vs帧0 MSE 序列检测周期）
        vs0 = np.array(report["mse"]["vs_frame0"], dtype=np.float64)
        peaks = find_period(vs0)
        stride = report["meta"]["stride"]
        report["autocorr"] = {
            "period_lag": peaks[0]["lag"] if peaks else None,
            "period_frames": (peaks[0]["lag"] * stride) if peaks else None,
            "confidence": peaks[0]["acf"] if peaks else 0.0,
            "all_peaks": peaks[:5],
        }

        # 主体大小趋势
        heights = np.array(report["subject_size"]["height"], dtype=np.float64)
        trend, slope, resid_lin, resid_quad = analyze_size_trend(heights)
        report["subject_size"].update(
            {
                "trend": trend,
                "slope_per_frame": slope,
                "fit_residual_linear": resid_lin,
                "fit_residual_quadratic": resid_quad,
            }
        )

    out = video_path.with_suffix(".analysis.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  分析报告已写入: {out}")

    if txt:
        print_ascii_summary(report)
    return out
