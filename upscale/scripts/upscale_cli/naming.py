from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path, PurePosixPath


def _component(value: str, fallback: str) -> str:
    leaf = PurePosixPath(str(value).replace("\\", "/")).name
    stem = Path(leaf).stem
    cleaned = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._")
    return cleaned or fallback


def _fps(value) -> str:
    try:
        fps = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return "unknown"
    if fps.denominator == 1:
        return str(fps.numerator)
    return f"{float(fps):.3f}".rstrip("0").rstrip(".")


def default_output_name(
    input_name: str,
    media_type: str,
    model: str,
    *,
    width: int | None,
    height: int | None,
    fps=None,
) -> str:
    source = _component(input_name, "input")
    model_name = _component(model, "model")
    if media_type == "image":
        size = f"{width}x{height}" if width and height else "unknown"
        return f"{source}_{size}_{model_name}.png"
    short_edge = min(int(width), int(height)) if width and height else "unknown"
    return f"{source}_{short_edge}p_{_fps(fps)}fps_{model_name}.mp4"
