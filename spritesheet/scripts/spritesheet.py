#!/usr/bin/env python3
"""Spritesheet Generator — 从视频中提取帧生成 spritesheet。

功能：
  - Chroma Key 抠图（HSV 色相分割），支持绿幕/蓝幕/白幕/黑幕自动检测
  - 循环检测：主体感知全局接缝检测（默认）或 CV 帧差法（--from-frame-zero）
  - 主体检测、居中对齐、色调归一化
  - 输出独立透明 PNG + spritesheet + player.html
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
ASSETS_DIR = SKILL_DIR / "assets"
CONFIG_SECTION = "spritesheet"

# 保险：确保同目录模块可 import（uv run spritesheet.py 时 sys.path[0] 已是 scripts/）
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from chroma import remove_bg_chroma  # noqa: E402
from loopdetect import detect_loop  # noqa: E402
from subject import detect_subject_info  # noqa: E402

# Chroma Key 抠图（BG_HSV_RANGES / detect_bg_color / remove_bg_chroma）已抽出到 chroma.py


# ─── 帧提取 ────────────────────────────────────────────────────────

def extract_frames(video_path: Path, num_frames: int, loop_start: int = 0, loop_end: int | None = None) -> list[np.ndarray]:
    """从视频中提取帧。loop_start/loop_end 指定循环区间。"""
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    end = min(loop_end + 1, total) if loop_end is not None else total
    span = end - loop_start

    if num_frames >= span:
        indices = list(range(loop_start, end))
    else:
        indices = [loop_start + int(i * span / num_frames) for i in range(num_frames)]

    frames = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            frames.append(frame)
            print(f"  提取帧 {len(frames)}/{num_frames}: index={idx}")
    cap.release()
    return frames


# 循环检测（find_loop_point_global / find_loop_point_cv / detect_loop）已抽出到 loopdetect.py


# ─── 主体处理 ──────────────────────────────────────────────────────
# detect_subject_info / detect_subject_bbox（兼容）已抽出到 subject.py


def compute_crop_box(frames: list[np.ndarray]) -> tuple[int, int, int, int]:
    """计算能完整显示所有帧主体 bbox 的最小并集框（无 padding）。

    返回 (x, y, w, h)：
      - (x, y) 是裁切框左上角（取所有帧 bbox xmin/ymin 的最小值）
      - (w, h) 是裁切框宽高（取所有帧 bbox xmax/ymax 的最大值后减去 x/y）
    """
    if not frames:
        return 0, 0, 0, 0

    x_min = y_min = float("inf")
    x_max = y_max = float("-inf")
    for frame in frames:
        info = detect_subject_info(frame[:, :, 3])
        bx, by, bw, bh = info.x, info.y, info.w, info.h
        x_min = min(x_min, bx)
        y_min = min(y_min, by)
        x_max = max(x_max, bx + bw)
        y_max = max(y_max, by + bh)

    return int(x_min), int(y_min), int(x_max - x_min), int(y_max - y_min)


def crop_frames(frames: list[np.ndarray], box: tuple[int, int, int, int]) -> list[np.ndarray]:
    """用同一个 box 裁切所有帧（np 切片，零像素插值、零开销）。"""
    x, y, w, h = box
    return [frame[y:y + h, x:x + w].copy() for frame in frames]


def write_metadata(
    output_dir: Path,
    box: tuple[int, int, int, int],
    cropped: list[np.ndarray],
    cols: int,
    video_path: Path,
    loop_start: int,
    loop_end: int,
    method: str,
) -> None:
    """写入 metadata.json（仅播放所需信息）。"""
    fh, fw = cropped[0].shape[:2]
    meta = {
        "frames": len(cropped),
        "cols": cols,
        "rows": (len(cropped) + cols - 1) // cols,
        "frame_w": fw,
        "frame_h": fh,
        "crop": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
        "loop": {"start": loop_start, "end": loop_end, "method": method},
        "video": str(video_path),
    }
    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  保存: {meta_path}")


def normalize_color(frames: list[np.ndarray]) -> list[np.ndarray]:
    """以第一帧为基准，统一色调。"""
    if len(frames) < 2:
        return frames

    ref_alpha = frames[0][:, :, 3]
    ref_bgr = frames[0][:, :, :3]
    ref_mean = cv2.mean(ref_bgr, ref_alpha.astype(np.uint8))[:3]

    result = [frames[0]]
    for frame in frames[1:]:
        alpha = frame[:, :, 3]
        mask = alpha > 0
        if not mask.any():
            result.append(frame)
            continue

        bgr = frame[:, :, :3].astype(np.float32)
        frame_mean = cv2.mean(bgr, alpha.astype(np.uint8))[:3]

        for c in range(3):
            ratio = ref_mean[c] / max(frame_mean[c], 1)
            bgr[:, :, c] = np.clip(bgr[:, :, c] * ratio, 0, 255)

        aligned = frame.copy()
        aligned[:, :, :3] = bgr.astype(np.uint8)
        result.append(aligned)

    return result


# ─── Spritesheet 生成 ──────────────────────────────────────────────

def create_spritesheet(frames: list[np.ndarray], cols: int = 4) -> np.ndarray:
    """将多帧合成为 spritesheet。"""
    h, w = frames[0].shape[:2]
    rows = (len(frames) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 4), dtype=np.uint8)
    for i, frame in enumerate(frames):
        r, c = divmod(i, cols)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = frame
    return sheet


# ─── Player HTML 生成 ──────────────────────────────────────────────

def generate_player(output_dir: Path, video_path: Path, cols: int, rows: int, total: int, frame_w: int, frame_h: int) -> None:
    """从模板生成 player.html。"""
    template_path = ASSETS_DIR / "player.html"
    if not template_path.exists():
        print(f"  警告: 播放器模板不存在: {template_path}")
        return

    html = template_path.read_text(encoding="utf-8")
    title = video_path.stem
    html = html.replace("{{TITLE}}", title)
    html = html.replace("{{COLS}}", str(cols))
    html = html.replace("{{ROWS}}", str(rows))
    html = html.replace("{{TOTAL}}", str(total))
    html = html.replace("{{FRAME_W}}", str(frame_w))
    html = html.replace("{{FRAME_H}}", str(frame_h))

    player_path = output_dir / "player.html"
    player_path.write_text(html, encoding="utf-8")
    print(f"  生成播放器: {player_path}")


# ─── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="从视频中提取帧生成 spritesheet",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--video", help="视频文件路径（--repack-dir 模式可不填）")
    parser.add_argument("--frames", type=int, default=8, help="提取帧数（默认 8）")
    parser.add_argument("--cols", type=int, default=4, help="spritesheet 列数（默认 4）")
    parser.add_argument("--bg-color", default="auto",
                        choices=["auto", "green", "blue", "white", "black"],
                        help="背景色（默认 auto 自动检测）")
    parser.add_argument("--from-frame-zero", action="store_true",
                        help="强制循环包含第 0 帧（旧 CV 帧差法，用作回退）")
    parser.add_argument("--analyze", action="store_true",
                        help="诊断模式：输出循环分析报告（.analysis.json），不生成 spritesheet")
    parser.add_argument("--txt", action="store_true",
                        help="配合 --analyze 打印 ASCII 摘要到 stdout")
    parser.add_argument("--loop-start", type=int, default=None,
                        help="手动指定循环起始帧（覆盖自动检测）")
    parser.add_argument("--loop-end", type=int, default=None,
                        help="手动指定循环结束帧（覆盖自动检测）")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认：视频同目录下）")
    parser.add_argument("--repack-dir", default=None,
                        help="重打包模式：指定已有输出目录，基于成品帧重新组合（与视频管线互斥）")
    parser.add_argument("--drop-frames", default=None,
                        help="重打包时删除指定帧（1-based，如 '15,16' 或 '15-16'，与 --keep-frames 互斥）")
    parser.add_argument("--keep-frames", default=None,
                        help="重打包时仅保留指定帧（1-based，如 '1-14'，与 --drop-frames 互斥）")

    args = parser.parse_args()

    # 重打包模式：不走视频管线
    if args.repack_dir:
        from repack import run_repack
        run_repack(
            args.repack_dir,
            drop=args.drop_frames,
            keep=args.keep_frames,
            cols=args.cols,
            video=args.video,
        )
        return

    # 其余模式需要 video
    if not args.video:
        parser.error("需要 --video（除非使用 --repack-dir）")

    # 互斥校验
    if (args.loop_start is None) != (args.loop_end is None):
        parser.error("--loop-start 与 --loop-end 必须同时指定")

    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"错误: 视频文件不存在: {video_path}", file=sys.stderr)
        sys.exit(1)

    print(f"视频: {video_path}")

    # 诊断模式：只输出分析报告，不走 spritesheet 管线
    if args.analyze:
        from analyze import run_analyze
        print("\n=== 诊断模式 --analyze ===")
        run_analyze(video_path, txt=args.txt, bg_color=args.bg_color)
        return

    # 步骤 1/9: 循环检测（优先级：manual > from-frame-zero > global）
    print("\n=== 步骤 1/9: 循环检测 ===")
    if args.loop_start is not None and args.loop_end is not None:
        mode, manual = "manual", (args.loop_start, args.loop_end)
        print(f"  使用手动指定循环区间: {args.loop_start}-{args.loop_end}")
    elif args.from_frame_zero:
        mode, manual = "from_frame_zero", None
    else:
        mode, manual = "global", None
    loop_start, loop_end, method = detect_loop(
        video_path, mode=mode, manual=manual, bg_color=args.bg_color
    )

    # 步骤 2/9: 抽帧
    print("\n=== 步骤 2/9: 抽帧 ===")
    raw_frames = extract_frames(video_path, args.frames, loop_start, loop_end)

    # 步骤 3/9: 抠图
    print("\n=== 步骤 3/9: 抠图 ===")
    transparent = [remove_bg_chroma(f, args.bg_color) for f in raw_frames]

    # 步骤 4/9: 色调归一化
    print("\n=== 步骤 4/9: 色调归一化 ===")
    color_normalized = normalize_color(transparent)
    print(f"  处理 {len(color_normalized)} 帧（{color_normalized[0].shape[1]} × {color_normalized[0].shape[0]}）")

    # 步骤 5/9: 算裁切框
    print("\n=== 步骤 5/9: 算裁切框 ===")
    box = compute_crop_box(color_normalized)
    print(f"  统一裁切框: x={box[0]}, y={box[1]}, w={box[2]}, h={box[3]}")

    # 步骤 6/9: 裁切
    print("\n=== 步骤 6/9: 裁切 ===")
    cropped = crop_frames(color_normalized, box)
    frame_w, frame_h = cropped[0].shape[1], cropped[0].shape[0]
    print(f"  裁切后每帧尺寸: {frame_w} × {frame_h}")

    # 生成输出目录名：[视频名]-[width]x[height]-[帧数]f
    if args.output_dir is None:
        dir_name = f"{video_path.stem}-{frame_w}x{frame_h}-{len(cropped)}f"
        output_dir = video_path.parent / dir_name
    else:
        output_dir = Path(args.output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"  输出: {output_dir}")

    # 步骤 7/9: 输出碎图
    print("\n=== 步骤 7/9: 输出碎图 ===")
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(cropped):
        path = frames_dir / f"frame_{i + 1:02d}.png"
        cv2.imwrite(str(path), frame)
    print(f"  保存: {frames_dir}/frame_01.png ~ frame_{len(cropped):02d}.png")

    # 步骤 8/9: 输出 spritesheet
    print("\n=== 步骤 8/9: 输出 spritesheet ===")
    rows = (len(cropped) + args.cols - 1) // args.cols
    sheet = create_spritesheet(cropped, cols=args.cols)
    sheet_path = output_dir / "spritesheet.png"
    cv2.imwrite(str(sheet_path), sheet)
    print(f"  保存: {sheet_path} ({sheet.shape[1]} × {sheet.shape[0]})")

    # 步骤 9/9: 输出 metadata + player
    print("\n=== 步骤 9/9: 输出 metadata + player ===")
    write_metadata(output_dir, box, cropped, args.cols, video_path, loop_start, loop_end, method)
    generate_player(output_dir, video_path, args.cols, rows, len(cropped), frame_w, frame_h)
    print(f"  保存: {output_dir}/metadata.json")
    print(f"  保存: {output_dir}/player.html")

    print(f"\n完成！共 {len(cropped)} 帧")
    print(f"  → {frames_dir}/frame_01.png ~ frame_{len(cropped):02d}.png（裁切后碎图）")
    print(f"  → {sheet_path}（整图）")
    print(f"  → {output_dir}/metadata.json")
    print(f"  → {output_dir}/player.html")


if __name__ == "__main__":
    main()
