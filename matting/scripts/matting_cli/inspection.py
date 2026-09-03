"""Bounded, deterministic image inspection used for method selection."""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any

from PIL import Image

from .errors import MattingError


def _ratio(value: float) -> float:
    return round(value, 6)


def inspect_image(
    path: str | Path, *, max_input_bytes: int, max_pixels: int
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MattingError(f"输入图片不存在: {source}")
    size_bytes = source.stat().st_size
    if size_bytes > max_input_bytes:
        raise MattingError(f"输入图片超过大小上限: {size_bytes} > {max_input_bytes}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as opened:
                opened.load()
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise MattingError(
                        f"输入图片像素超过上限: {width}x{height} > {max_pixels}"
                    )
                original_mode = opened.mode
                has_alpha_channel = (
                    "A" in opened.getbands() or "transparency" in opened.info
                )
                rgba = opened.convert("RGBA")
                alpha = rgba.getchannel("A") if has_alpha_channel else None
    except MattingError:
        raise
    except Exception as exc:
        raise MattingError(f"无法解码输入图片 {source}: {exc}") from exc

    total = width * height
    alpha_histogram = alpha.histogram() if alpha is not None else [0] * 256
    transparent_ratio = alpha_histogram[0] / total if alpha is not None else 0.0
    semitransparent_ratio = (
        sum(alpha_histogram[1:255]) / total if alpha is not None else 0.0
    )
    has_effective_alpha = transparent_ratio >= 0.001 or semitransparent_ratio >= 0.001

    sample = rgba.convert("RGB")
    sample.thumbnail((512, 512), Image.Resampling.LANCZOS)
    sw, sh = sample.size
    border = []
    for x in range(sw):
        border.append(sample.getpixel((x, 0)))
        if sh > 1:
            border.append(sample.getpixel((x, sh - 1)))
    for y in range(1, max(1, sh - 1)):
        border.append(sample.getpixel((0, y)))
        if sw > 1:
            border.append(sample.getpixel((sw - 1, y)))
    border = border or [sample.getpixel((0, 0))]
    mean = tuple(
        round(sum(pixel[index] for pixel in border) / len(border)) for index in range(3)
    )
    distances = [math.dist(pixel, mean) for pixel in border]
    uniformity = sum(distance <= 24 for distance in distances) / len(distances)
    luminances = [(0.2126 * r) + (0.7152 * g) + (0.0722 * b) for r, g, b in border]
    border_dark_ratio = sum(value <= 45 for value in luminances) / len(luminances)
    border_light_ratio = sum(value >= 210 for value in luminances) / len(luminances)

    flattened = getattr(sample, "get_flattened_data", None)
    pixels = list(flattened() if flattened is not None else sample.getdata())
    global_luma = [(0.2126 * r) + (0.7152 * g) + (0.0722 * b) for r, g, b in pixels]
    bright_ratio = sum(value >= 180 for value in global_luma) / len(global_luma)
    dark_ratio = sum(value <= 70 for value in global_luma) / len(global_luma)
    edge_total = 0
    edge_count = 0
    gray = [int(value) for value in global_luma]
    for y in range(sh):
        row = y * sw
        for x in range(sw):
            current = gray[row + x]
            if x + 1 < sw:
                edge_total += abs(current - gray[row + x + 1])
                edge_count += 1
            if y + 1 < sh:
                edge_total += abs(current - gray[row + sw + x])
                edge_count += 1
    edge_density = edge_total / max(1, edge_count) / 255.0

    r, g, b = mean
    screen_color = None
    if uniformity >= 0.82 and g >= 80 and g >= max(r, b) * 1.25:
        screen_color = "green"
    elif uniformity >= 0.82 and b >= 80 and b >= max(r, g) * 1.25:
        screen_color = "blue"
    likely_luma = (border_dark_ratio >= 0.8 and 0.01 <= bright_ratio <= 0.65) or (
        border_light_ratio >= 0.8 and 0.01 <= dark_ratio <= 0.65
    )

    return {
        "path": str(source),
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "mode": original_mode,
        "has_alpha_channel": has_alpha_channel,
        "has_effective_alpha": has_effective_alpha,
        "transparent_ratio": _ratio(transparent_ratio),
        "semitransparent_ratio": _ratio(semitransparent_ratio),
        "border_color_hex": "#%02X%02X%02X" % mean,
        "border_uniformity": _ratio(uniformity),
        "border_dark_ratio": _ratio(border_dark_ratio),
        "border_light_ratio": _ratio(border_light_ratio),
        "global_bright_ratio": _ratio(bright_ratio),
        "global_dark_ratio": _ratio(dark_ratio),
        "edge_density": _ratio(edge_density),
        "likely_uniform_background": uniformity >= 0.9,
        "screen_color": screen_color,
        "likely_luma_effect": likely_luma,
    }
