#!/usr/bin/env python3
"""Spritesheet Generator — 从视频中提取帧生成 spritesheet。

功能：
  - Chroma Key 抠图（HSV 色相分割），支持绿幕/蓝幕/白幕/黑幕自动检测
  - 混合循环检测：CV 帧差法（默认）或视频模型语义分析（--smart）
  - 主体检测、居中对齐、色调归一化
  - 输出独立透明 PNG + spritesheet + player.html
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
ASSETS_DIR = SKILL_DIR / "assets"
CONFIG_SECTION = "spritesheet"

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

    # 反转：背景=0（透明），前景=255
    mask = cv2.bitwise_not(mask)

    # 形态学清理
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    # 边缘羽化（消除锯齿和白边）
    mask = cv2.GaussianBlur(mask, (3, 3), 0)

    bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = mask
    return bgra


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


# ─── 循环检测 ──────────────────────────────────────────────────────

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
    """改进的循环检测：找"视觉变化最丰富的完整周期"。

    策略：
    1. 计算全视频 MSE 曲线（每帧 vs 第 0 帧）
    2. 在曲线上找所有"回归点"（MSE 从上升转为下降的局部极大值之后的极小值）
    3. 对每个候选周期，计算"丰富度"= 周期内逐帧差分的累计和
    4. 选丰富度最高且回归相似度合理的周期
    5. 若无明确周期，回退到取整段视频
    """
    grays, mse_series = _compute_frame_mse_series(video_path)
    total = len(grays)

    if total < 4:
        return 0, total - 1

    # 最小周期长度：至少 0.3 秒或视频的 15%
    fps = cv2.VideoCapture(str(video_path)).get(cv2.CAP_PROP_FPS)
    min_cycle = max(int(fps * 0.3), total // 7, 4)

    # 逐帧差分（相邻帧变化量）
    frame_diffs = np.array([
        np.mean((grays[i] - grays[i + 1]) ** 2) / (255 ** 2)
        for i in range(total - 1)
    ])

    # 找 MSE 曲线的局部极小值（回归点 = 与起始帧相似的帧）
    candidates = []

    for i in range(min_cycle, total):
        # 检查是否为局部极小值（前后窗口）
        window = max(3, min_cycle // 4)
        lo = max(0, i - window)
        hi = min(total, i + window + 1)
        if mse_series[i] == np.min(mse_series[lo:hi]):
            # 计算这个周期的"丰富度"：区间内逐帧差分的累计和
            richness = float(np.sum(frame_diffs[0:i]))
            # 起止帧相似度
            similarity = 1.0 - mse_series[i]
            # 综合评分：丰富度 × 相似度权重
            score = richness * (0.5 + 0.5 * similarity)
            candidates.append({
                "start": 0,
                "end": i,
                "richness": richness,
                "similarity": similarity,
                "score": score,
            })

    if not candidates:
        # 没找到明确周期，用整段视频
        print(f"  CV 循环检测: 未找到明确周期，使用整段视频 (0-{total - 1})")
        return 0, total - 1

    # 选评分最高的候选
    best = max(candidates, key=lambda c: c["score"])
    print(f"  CV 循环检测: 起点={best['start']}, 终点={best['end']} "
          f"(丰富度={best['richness']:.5f}, 相似度={best['similarity']:.3f}, "
          f"候选数={len(candidates)})")
    return best["start"], best["end"]


def find_loop_point_smart(video_path: Path) -> tuple[int, int]:
    """视频模型分析：调用 video-analyzer 分析最佳循环区间。"""
    from openai import OpenAI

    # 加载配置
    config_path = _find_config()
    if not config_path:
        print("  --smart 模式需要 agent_config.toml，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    sprite_cfg = config.get(CONFIG_SECTION, {})
    model_name = sprite_cfg.get("model", "")

    # 读取 video-analyzer 的模型配置
    va_cfg = config.get("video-analyzer", {})
    model_cfg = va_cfg.get("models", {}).get(model_name)
    if not model_cfg:
        print(f"  未找到模型 '{model_name}'，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    # 获取视频信息（用于提示词）
    cap_info = cv2.VideoCapture(str(video_path))
    total = int(cap_info.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap_info.get(cv2.CAP_PROP_FPS)
    cap_info.release()

    # 准备视频
    import base64, tempfile

    with tempfile.TemporaryDirectory(prefix="spritesheet-") as tmp_dir:
        dest = Path(tmp_dir) / "video.mp4"
        import shutil
        shutil.copy2(video_path, dest)

        supports_video = model_cfg.get("supports_video", False)
        if supports_video:
            video_b64 = base64.b64encode(dest.read_bytes()).decode()
            content = [
                {"type": "input_video", "video_url": f"data:video/mp4;base64,{video_b64}"},
            ]
        else:
            # 抽帧
            cap = cv2.VideoCapture(str(dest))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            indices = [int(i * total / 8) for i in range(8)]
            content = []
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    _, buf = cv2.imencode(".jpg", frame)
                    b64 = base64.b64encode(buf).decode()
                    content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})
            cap.release()

        content.append({
            "type": "input_text",
            "text": (
                "你是一个 spritesheet 动画专家。我需要从这段视频中提取帧，制作流畅的循环动画。\n\n"
                "请先完整观看所有帧，理解视频的整体运动模式，然后回答：\n\n"
                "1. 视频的主体是什么？有哪些可见的运动或变化？\n"
                "2. 这些运动的周期大约是多少帧？\n"
                "3. 从全视频范围来看，哪段区间最适合做循环动画？\n\n"
                "关键要求：\n"
                "- 区间必须足够长，使得均匀抽取的帧之间有明显的视觉变化（否则动画会卡顿）\n"
                "- 区间的首帧和尾帧应该视觉上相似，能自然衔接形成循环\n"
                "- 优先选择运动最丰富、变化最明显的周期\n"
                "- 不要只看视频开头，要扫描全视频找最佳区间\n\n"
                f"视频总帧数: {total}，帧率: {fps:.0f}fps，时长: {total/fps:.1f}秒\n"
                "请以 JSON 格式回答："
                "{\"loop_start\": 起始帧序号, \"loop_end\": 结束帧序号, "
                "\"recommended_frames\": 推荐帧数(≤12), "
                "\"motion_description\": 运动描述, "
                "\"reason\": 选择该区间的理由}。"
                "只需输出 JSON，不要其他内容。"
            ),
        })

    # 调用 API
    api_key = model_cfg.get("api_key", "") or os.getenv(model_cfg.get("api_key_env", ""), "")
    if not api_key:
        print("  API Key 未配置，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    import httpx
    client = OpenAI(base_url=model_cfg["base_url"], api_key=api_key,
                     timeout=httpx.Timeout(300.0, connect=60.0))
    api_type = model_cfg.get("api_type", "responses")
    messages = [{"role": "user", "content": content}]

    print(f"  正在调用视频模型分析循环区间: {model_cfg['model']}")
    if api_type == "responses":
        response = client.responses.create(model=model_cfg["model"], input=messages)
        result = response.output_text if hasattr(response, "output_text") else str(response)
    else:
        response = client.chat.completions.create(model=model_cfg["model"], messages=messages)
        result = response.choices[0].message.content

    # 解析结果
    try:
        # 提取 JSON
        match = re.search(r"\{[^}]+\}", result)
        if match:
            data = json.loads(match.group())
            loop_start = int(data.get("loop_start", 0))
            loop_end = int(data.get("loop_end", 0))
            rec_frames = int(data.get("recommended_frames", 8))
            reason = data.get("reason", "")
            print(f"  模型建议: 起点={loop_start}, 终点={loop_end}, 推荐帧数={rec_frames}")
            if reason:
                print(f"  理由: {reason}")
            return loop_start, loop_end
    except (json.JSONDecodeError, ValueError) as e:
        print(f"  模型返回解析失败: {e}")

    print("  回退到 CV 帧差法")
    return find_loop_point_cv(video_path)


def _find_config() -> Path | None:
    """查找 agent_config.toml（四位置发现策略）。"""
    candidates = [
        Path.cwd() / "agent_config.toml",
        SKILL_DIR / "agent_config.toml",
    ]
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            candidates.append(parent / "agent_config.toml")
            break
    candidates.append(Path.home() / ".agents" / "agent_config.toml")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


# ─── 主体处理 ──────────────────────────────────────────────────────

def detect_subject_bbox(alpha_channel: np.ndarray) -> tuple[int, int, int, int]:
    """检测主体边界框 (x, y, w, h)。"""
    coords = cv2.findNonZero(alpha_channel)
    if coords is None:
        return 0, 0, alpha_channel.shape[1], alpha_channel.shape[0]
    return cv2.boundingRect(coords)


def align_and_normalize(frame: np.ndarray, target_size: int) -> np.ndarray:
    """将主体居中并对齐到统一尺寸。"""
    x, y, w, h = detect_subject_bbox(frame[:, :, 3])
    subject = frame[y:y + h, x:x + w]

    # 缩放（留 10% 边距）
    max_dim = max(w, h)
    scale = (target_size * 0.9) / max_dim
    new_w, new_h = int(w * scale), int(h * scale)
    subject_resized = cv2.resize(subject, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    # 居中放置
    canvas = np.zeros((target_size, target_size, 4), dtype=np.uint8)
    x_off = (target_size - new_w) // 2
    y_off = (target_size - new_h) // 2
    canvas[y_off:y_off + new_h, x_off:x_off + new_w] = subject_resized
    return canvas


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
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--frames", type=int, default=8, help="提取帧数（默认 8）")
    parser.add_argument("--cols", type=int, default=4, help="spritesheet 列数（默认 4）")
    parser.add_argument("--canvas-size", type=int, default=512, help="输出帧画布尺寸（默认 512）")
    parser.add_argument("--bg-color", default="auto",
                        choices=["auto", "green", "blue", "white", "black"],
                        help="背景色（默认 auto 自动检测）")
    parser.add_argument("--smart", action="store_true", help="使用视频模型分析最佳循环区间")
    parser.add_argument("--output-dir", default="./spritesheet_output", help="输出目录")

    args = parser.parse_args()
    video_path = Path(args.video).expanduser().resolve()
    output_dir = Path(args.output_dir)

    if not video_path.exists():
        print(f"错误: 视频文件不存在: {video_path}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"视频: {video_path}")
    print(f"输出: {output_dir}")

    # 步骤 1: 循环检测
    print("\n=== 步骤 1/5: 循环检测 ===")
    if args.smart:
        loop_start, loop_end = find_loop_point_smart(video_path)
    else:
        loop_start, loop_end = find_loop_point_cv(video_path)

    # 步骤 2: 提取帧
    print(f"\n=== 步骤 2/5: 提取 {args.frames} 帧 ===")
    raw_frames = extract_frames(video_path, args.frames, loop_start, loop_end)

    # 步骤 3: 去背景
    print(f"\n=== 步骤 3/5: 去除背景 (模式: {args.bg_color}) ===")
    transparent = [remove_bg_chroma(f, args.bg_color) for f in raw_frames]

    # 步骤 4: 色调归一化 + 对齐
    print("\n=== 步骤 4/5: 色调归一化 + 对齐 ===")
    color_normalized = normalize_color(transparent)
    aligned = [align_and_normalize(f, args.canvas_size) for f in color_normalized]

    # 步骤 5: 输出
    print("\n=== 步骤 5/5: 输出文件 ===")
    for i, frame in enumerate(aligned):
        path = output_dir / f"frame_{i + 1:02d}.png"
        cv2.imwrite(str(path), frame)
        print(f"  保存: {path}")

    rows = (len(aligned) + args.cols - 1) // args.cols
    sheet = create_spritesheet(aligned, cols=args.cols)
    sheet_path = output_dir / "spritesheet.png"
    cv2.imwrite(str(sheet_path), sheet)
    print(f"  保存: {sheet_path}")

    generate_player(output_dir, video_path, args.cols, rows, len(aligned),
                    args.canvas_size, args.canvas_size)

    print(f"\n完成！共 {len(aligned)} 帧")
    print(f"  → {output_dir}/frame_01.png ~ frame_{len(aligned):02d}.png")
    print(f"  → {sheet_path}")
    print(f"  → {output_dir}/player.html")


if __name__ == "__main__":
    main()
