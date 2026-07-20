"""Solid chroma-key removal helper migrated from the system imagegen skill."""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median

from PIL import Image, ImageFilter

from imggen.models import ImggenError


Color = tuple[int, int, int]
KEY_DOMINANCE_THRESHOLD = 16.0


def remove_chroma_key(
    input_path: str,
    output_path: str,
    *,
    key_color: str = "#00ff00",
    tolerance: int = 12,
    auto_key: str = "none",
    soft_matte: bool = False,
    transparent_threshold: float = 12.0,
    opaque_threshold: float = 96.0,
    edge_feather: float = 0.0,
    edge_contract: int = 0,
    spill_cleanup: bool = False,
    force: bool = False,
) -> dict[str, int | str]:
    source = Path(input_path).expanduser()
    output = Path(output_path).expanduser()
    _validate(
        source,
        output,
        tolerance,
        auto_key,
        soft_matte,
        transparent_threshold,
        opaque_threshold,
        edge_feather,
        edge_contract,
        force,
    )
    with Image.open(source) as image:
        rgba = image.convert("RGBA")
    key = _sample_key(rgba, auto_key) if auto_key != "none" else _parse_color(key_color)
    pixels = rgba.load()
    transparent_before = 0
    for y in range(rgba.height):
        for x in range(rgba.width):
            red, green, blue, original_alpha = pixels[x, y]
            rgb = (red, green, blue)
            distance = max(abs(rgb[index] - key[index]) for index in range(3))
            key_like = _looks_key_colored(rgb, key, distance)
            if soft_matte and key_like:
                alpha = min(
                    _soft_alpha(distance, transparent_threshold, opaque_threshold),
                    _dominance_alpha(rgb, key),
                )
            else:
                alpha = 0 if distance <= tolerance else 255
            alpha = round(alpha * original_alpha / 255)
            if alpha <= 8:
                alpha = 0
            if alpha == 0:
                pixels[x, y] = (0, 0, 0, 0)
                transparent_before += 1
            else:
                if spill_cleanup and key_like and alpha < 252:
                    red, green, blue = _despill(rgb, key)
                pixels[x, y] = (red, green, blue, alpha)
    if edge_contract:
        alpha = rgba.getchannel("A")
        for _ in range(edge_contract):
            alpha = alpha.filter(ImageFilter.MinFilter(3))
        rgba.putalpha(alpha)
    if edge_feather:
        rgba.putalpha(
            rgba.getchannel("A").filter(ImageFilter.GaussianBlur(radius=edge_feather))
        )
    alpha_channel = rgba.getchannel("A")
    getter = getattr(alpha_channel, "get_flattened_data", alpha_channel.getdata)
    alphas = list(getter())
    output.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(output, format="PNG" if output.suffix.lower() == ".png" else "WEBP")
    return {
        "output": str(output.resolve()),
        "key_color": f"#{key[0]:02x}{key[1]:02x}{key[2]:02x}",
        "transparent_pixels": sum(value == 0 for value in alphas),
        "partial_pixels": sum(0 < value < 255 for value in alphas),
        "total_pixels": len(alphas),
        "matched_before_feather": transparent_before,
    }


def _validate(
    source: Path,
    output: Path,
    tolerance: int,
    auto_key: str,
    soft_matte: bool,
    transparent_threshold: float,
    opaque_threshold: float,
    edge_feather: float,
    edge_contract: int,
    force: bool,
) -> None:
    if not source.is_file():
        raise ImggenError(f"输入图片不存在: {source}")
    if output.suffix.lower() not in {".png", ".webp"}:
        raise ImggenError("chroma-key 输出必须是 .png 或 .webp")
    if output.exists() and not force:
        raise ImggenError(f"输出已存在: {output}（使用 --force 覆盖）")
    if not 0 <= tolerance <= 255:
        raise ImggenError("--tolerance 必须在 0 到 255 之间")
    if auto_key not in {"none", "corners", "border"}:
        raise ImggenError("--auto-key 必须是 none/corners/border")
    if soft_matte and not 0 <= transparent_threshold < opaque_threshold <= 255:
        raise ImggenError("soft matte 阈值必须满足 0 <= transparent < opaque <= 255")
    if not 0 <= edge_feather <= 64:
        raise ImggenError("--edge-feather 必须在 0 到 64 之间")
    if not 0 <= edge_contract <= 16:
        raise ImggenError("--edge-contract 必须在 0 到 16 之间")


def _parse_color(value: str) -> Color:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value.strip())
    if not match:
        raise ImggenError("--key-color 必须是 #00ff00 形式的十六进制 RGB")
    raw = match.group(1)
    return int(raw[:2], 16), int(raw[2:4], 16), int(raw[4:], 16)


def _sample_key(image: Image.Image, mode: str) -> Color:
    samples: list[Color] = []
    pixels = image.load()
    if mode == "corners":
        patch = max(1, min(image.width, image.height, 12))
        boxes = (
            (0, 0, patch, patch),
            (image.width - patch, 0, image.width, patch),
            (0, image.height - patch, patch, image.height),
            (image.width - patch, image.height - patch, image.width, image.height),
        )
        for left, top, right, bottom in boxes:
            samples.extend(
                pixels[x, y][:3] for y in range(top, bottom) for x in range(left, right)
            )
    else:
        band = max(1, min(image.width, image.height, 6))
        step = max(1, min(image.width, image.height) // 256)
        for x in range(0, image.width, step):
            for y in range(band):
                samples.extend((pixels[x, y][:3], pixels[x, image.height - 1 - y][:3]))
        for y in range(0, image.height, step):
            for x in range(band):
                samples.extend((pixels[x, y][:3], pixels[image.width - 1 - x, y][:3]))
    return tuple(
        round(median(sample[channel] for sample in samples)) for channel in range(3)
    )  # type: ignore[return-value]


def _despill(rgb: Color, key: Color) -> Color:
    dominant = _spill_channels(key)
    if not dominant:
        return rgb
    channels = list(rgb)
    anchor = max(
        (channels[index] for index in range(3) if index not in dominant), default=0
    )
    for index in dominant:
        channels[index] = min(channels[index], max(0, anchor - 1))
    return channels[0], channels[1], channels[2]


def _soft_alpha(distance: int, transparent: float, opaque: float) -> int:
    if distance <= transparent:
        return 0
    if distance >= opaque:
        return 255
    ratio = (distance - transparent) / (opaque - transparent)
    smooth = ratio * ratio * (3.0 - 2.0 * ratio)
    return max(0, min(255, round(255 * smooth)))


def _spill_channels(key: Color) -> list[int]:
    strongest = max(key)
    if strongest < 128:
        return []
    return [
        index
        for index, value in enumerate(key)
        if value >= strongest - 16 and value >= 128
    ]


def _key_dominance(rgb: Color, key: Color) -> float:
    spill = _spill_channels(key)
    if not spill:
        return 0.0
    non_spill = [index for index in range(3) if index not in spill]
    key_strength = (
        min(rgb[index] for index in spill) if len(spill) > 1 else rgb[spill[0]]
    )
    return float(key_strength - max((rgb[index] for index in non_spill), default=0))


def _looks_key_colored(rgb: Color, key: Color, distance: int) -> bool:
    return (
        distance <= 32
        or not _spill_channels(key)
        or _key_dominance(rgb, key) >= KEY_DOMINANCE_THRESHOLD
    )


def _dominance_alpha(rgb: Color, key: Color) -> int:
    spill = _spill_channels(key)
    if not spill:
        return 255
    non_spill = [index for index in range(3) if index not in spill]
    key_strength = (
        min(rgb[index] for index in spill) if len(spill) > 1 else rgb[spill[0]]
    )
    non_key_strength = max((rgb[index] for index in non_spill), default=0)
    dominance = key_strength - non_key_strength
    if dominance <= 0:
        return 255
    denominator = max(1.0, float(max(key)) - non_key_strength)
    return max(0, min(255, round((1.0 - min(1.0, dominance / denominator)) * 255)))
