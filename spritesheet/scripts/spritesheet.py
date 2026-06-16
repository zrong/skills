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
    """视频模型分析：调用火山方舟视觉大模型分析最佳循环区间。

    根据配置的 API 端点自动选择传入方式：
    - 标准端点 /api/v3: 使用 input_video 直接传入视频（base64），平台自动按 fps 抽帧
    - Coding plan /api/coding/v3: 使用 input_image 手动抽帧后以图片方式传入

    API 文档：
    - 视频理解教程: https://www.volcengine.com/docs/82379/1895586
    - Responses API: https://www.volcengine.com/docs/82379/1569618
    - Chat API: https://www.volcengine.com/docs/82379/1494384
    - 模型列表: https://www.volcengine.com/docs/82379/1330310

    input_video 传入限制（标准端点）：
    - base64 编码: 视频 ≤ 50MB，请求体 ≤ 64MB
    - Files API 上传: 视频 ≤ 512MB（默认存储）或 ≤ 2GB（TOS 存储）
    - 视频 URL: 视频 ≤ 50MB，需公网可访问
    - fps 范围: [0.2, 5.0]，最高 5fps
    - doubao-seed-2.0 最大抽帧数: 1280 帧（80k tokens ÷ 64 tokens/帧）

    input_image 传入限制（coding plan 端点）：
    - 手动用 OpenCV 按每秒 5 帧抽帧，以 base64 JPEG 图片传入
    - 无硬性帧数限制，但受模型上下文窗口约束

    支持两种 API 格式（通过 agent_config.toml 的 api_type 配置）：
    - Responses API (api_type="responses")
    - Chat API (api_type="chat")
    """
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

    # 读取模型配置（从 video-analyzer.models.{model_name} 获取）
    va_cfg = config.get("video-analyzer", {})
    model_cfg = va_cfg.get("models", {}).get(model_name)
    if not model_cfg:
        print(f"  未找到模型 '{model_name}'，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    # 获取视频元信息
    cap_info = cv2.VideoCapture(str(video_path))
    total = int(cap_info.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = cap_info.get(cv2.CAP_PROP_FPS)
    cap_info.release()
    duration = total / video_fps

    # ─── 判断端点类型 ────────────────────────────────────────────────
    # /api/coding/v3 不支持 input_video，需手动抽帧以 input_image 传入
    # /api/v3 支持 input_video，直接传入视频由平台自动抽帧
    base_url = model_cfg["base_url"]
    use_native_video = "/api/coding/v3" not in base_url

    # ─── 提示词 ──────────────────────────────────────────────────────
    prompt_text = (
        "你是一个 spritesheet 动画专家。我需要从这段视频中提取帧，制作流畅的循环动画。\n\n"
        "请先完整观看视频，理解整体运动模式，然后回答：\n\n"
        "1. 视频的主体是什么？有哪些可见的运动或变化？\n"
        "2. 这些运动的周期大约是多少秒？\n"
        "3. 从全视频范围来看，哪段区间最适合做循环动画？\n\n"
        "关键要求：\n"
        "- 区间必须足够长，使得均匀抽取的帧之间有明显的视觉变化（否则动画会卡顿）\n"
        "- 区间的首帧和尾帧应该视觉上相似，能自然衔接形成循环\n"
        "- 优先选择运动最丰富、变化最明显的周期\n"
        "- 不要只看视频开头，要扫描全视频找最佳区间\n\n"
        f"视频帧率: {video_fps:.0f}fps，时长: {duration:.1f}秒\n"
        "请以 JSON 格式回答："
        "{\"loop_start\": 起始帧序号, \"loop_end\": 结束帧序号, "
        "\"recommended_frames\": 推荐帧数(≤12), "
        "\"motion_description\": 运动描述, "
        "\"reason\": 选择该区间的理由}。"
        "只需输出 JSON，不要其他内容。"
    )

    # ─── API 客户端初始化 ────────────────────────────────────────────
    api_key = model_cfg.get("api_key", "") or os.getenv(model_cfg.get("api_key_env", ""), "")
    if not api_key:
        print("  API Key 未配置，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    import httpx
    import base64

    client = OpenAI(base_url=base_url, api_key=api_key,
                     timeout=httpx.Timeout(300.0, connect=60.0))
    api_type = model_cfg.get("api_type", "responses")

    # ─── 构建请求内容 ────────────────────────────────────────────────
    if use_native_video:
        # ── 标准端点 /api/v3: 使用 input_video 直接传入视频 ──────────
        # fps 范围 [0.2, 5.0]，取最大值 5 以获得最精细的运动分析
        # 文档: https://www.volcengine.com/docs/82379/1895586#bf4d9224
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 50:
            print(f"  视频 {file_size_mb:.1f}MB 超过 base64 限制(50MB)，回退到 CV 帧差法")
            return find_loop_point_cv(video_path)

        with open(video_path, "rb") as f:
            video_b64 = base64.b64encode(f.read()).decode()

        suffix = video_path.suffix.lower()
        mime_map = {".mp4": "video/mp4", ".avi": "video/avi", ".mov": "video/mov"}
        mime_type = mime_map.get(suffix, "video/mp4")

        smart_fps = 5
        if api_type == "responses":
            content = [
                {"type": "input_video", "video_url": f"data:{mime_type};base64,{video_b64}", "fps": smart_fps},
                {"type": "input_text", "text": prompt_text},
            ]
        else:
            content = [
                {"type": "video_url", "video_url": {"url": f"data:{mime_type};base64,{video_b64}", "fps": smart_fps}},
                {"type": "text", "text": prompt_text},
            ]
        messages = [{"role": "user", "content": content}]
        print(f"  正在调用视频模型分析循环区间: {model_cfg['model']} [input_video, fps={smart_fps}]")
        print(f"  视频: {file_size_mb:.1f}MB, {duration:.1f}秒")

    else:
        # ── Coding plan /api/coding/v3: 手动抽帧以 input_image 传入 ──
        # 每秒抽 5 帧（匹配 API 的 fps 上限），最少 6 帧
        sample_fps = 5
        sample_count = max(int(duration * sample_fps), 6)
        indices = [int(i * total / sample_count) for i in range(sample_count)]

        content = []
        for idx in indices:
            cap_info = cv2.VideoCapture(str(video_path))
            cap_info.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap_info.read()
            cap_info.release()
            if ret:
                _, buf = cv2.imencode(".jpg", frame)
                img_b64 = base64.b64encode(buf).decode()
                if api_type == "responses":
                    content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{img_b64}"})
                else:
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})

        if api_type == "responses":
            content.append({"type": "input_text", "text": prompt_text})
        else:
            content.append({"type": "text", "text": prompt_text})

        messages = [{"role": "user", "content": content}]
        print(f"  正在调用视频模型分析循环区间: {model_cfg['model']} [input_image, {len(indices)}帧]")
        print(f"  视频: {duration:.1f}秒, 抽帧fps={sample_fps}")

    # ─── 调用 API ────────────────────────────────────────────────────
    try:
        if api_type == "responses":
            response = client.responses.create(model=model_cfg["model"], input=messages)
            result = response.output_text if hasattr(response, "output_text") else str(response)
        else:
            response = client.chat.completions.create(model=model_cfg["model"], messages=messages)
            result = response.choices[0].message.content
    except Exception as e:
        print(f"  API 调用失败: {e}，回退到 CV 帧差法")
        return find_loop_point_cv(video_path)

    # 解析结果
    try:
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
        bx, by, bw, bh = detect_subject_bbox(frame[:, :, 3])
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
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--frames", type=int, default=8, help="提取帧数（默认 8）")
    parser.add_argument("--cols", type=int, default=4, help="spritesheet 列数（默认 4）")
    parser.add_argument("--bg-color", default="auto",
                        choices=["auto", "green", "blue", "white", "black"],
                        help="背景色（默认 auto 自动检测）")
    parser.add_argument("--smart", action="store_true", help="使用视频模型分析最佳循环区间")
    parser.add_argument("--loop-start", type=int, default=None,
                        help="手动指定循环起始帧（覆盖自动检测）")
    parser.add_argument("--loop-end", type=int, default=None,
                        help="手动指定循环结束帧（覆盖自动检测）")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认：视频同目录下）")

    args = parser.parse_args()
    video_path = Path(args.video).expanduser().resolve()

    if not video_path.exists():
        print(f"错误: 视频文件不存在: {video_path}", file=sys.stderr)
        sys.exit(1)

    print(f"视频: {video_path}")

    # 步骤 1/9: 循环检测
    print("\n=== 步骤 1/9: 循环检测 ===")
    if args.loop_start is not None and args.loop_end is not None:
        loop_start, loop_end = args.loop_start, args.loop_end
        print(f"  使用手动指定循环区间: {loop_start}-{loop_end}")
    elif args.smart:
        loop_start, loop_end = find_loop_point_smart(video_path)
    else:
        loop_start, loop_end = find_loop_point_cv(video_path)

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
    method = "smart" if args.smart else "cv"
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
