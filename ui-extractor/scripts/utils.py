"""Utility functions for UI Extractor.

Provides:
- Image I/O helpers (read with BGR->RGB conversion, write with auto directory creation)
- Bounding box math utilities
- Filter helpers (area, aspect ratio, circularity)
- Visualization helpers (annotation drawing, mask saving)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ─── BGR/RGB convention ────────────────────────────────────────────
# OpenCV reads/writes images in BGR. For most external usage (PIL, HTML, JSON
# metadata) we use RGB. Helpers in this module normalise at the boundary.


def read_image(path: str | Path, mode: str = "color") -> np.ndarray:
    """Read image from disk.

    Args:
        path: Input file path.
        mode: 'color' (BGR), 'rgb' (RGB), or 'gray'.

    Returns:
        Image array.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")
    if mode == "gray":
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    else:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Failed to decode image: {path}")
        if mode == "rgb":
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if img is None:
        raise ValueError(f"Failed to decode image: {path}")
    return img


def write_image(path: str | Path, image: np.ndarray) -> None:
    """Write image to disk (auto-create parent directory).

    Accepts BGR (3-channel) or BGRA (4-channel) images.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise IOError(f"Failed to write image: {path}")
    logger.info("Wrote %s (%s)", path, image.shape)


def write_text(path: str | Path, text: str) -> None:
    """Write text to disk (auto-create parent directory)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_dir(path: str | Path) -> Path:
    """Create directory (including parents) if it does not exist, return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ─── Element data class ────────────────────────────────────────────


@dataclass
class Element:
    """A separated UI element cropped from the source image."""

    index: int
    bbox: tuple[int, int, int, int]  # x, y, w, h
    area: int
    aspect_ratio: float
    circularity: float
    image: np.ndarray  # BGRA crop

    def to_dict(self, include_image_path: str | None = None) -> dict:
        d = {
            "index": self.index,
            "bbox": list(self.bbox),
            "area": self.area,
            "aspect_ratio": round(self.aspect_ratio, 3),
            "circularity": round(self.circularity, 3),
        }
        if include_image_path:
            d["image"] = include_image_path
        return d


# ─── Contour filtering helpers ─────────────────────────────────────


def contour_area(cnt: np.ndarray) -> float:
    return float(cv2.contourArea(cnt))


def contour_bbox(cnt: np.ndarray) -> tuple[int, int, int, int]:
    return cv2.boundingRect(cnt)


def contour_aspect_ratio(cnt: np.ndarray) -> float:
    _, _, w, h = contour_bbox(cnt)
    if w == 0 or h == 0:
        return float("inf")
    return max(w, h) / min(w, h)


def contour_circularity(cnt: np.ndarray) -> float:
    """4*pi*area / perimeter^2. 1.0 = perfect circle, 0 = line."""
    area = cv2.contourArea(cnt)
    perimeter = cv2.arcLength(cnt, True)
    if perimeter == 0:
        return 0.0
    return float(4 * np.pi * area / (perimeter * perimeter))


def filter_contours(
    contours: Iterable[np.ndarray],
    min_area: int = 500,
    max_aspect: float = 10.0,
) -> list[np.ndarray]:
    """Filter contours by area and aspect ratio. Returns list of valid contours."""
    valid = []
    for cnt in contours:
        if contour_area(cnt) < min_area:
            continue
        if contour_aspect_ratio(cnt) > max_aspect:
            continue
        valid.append(cnt)
    return valid


# ─── Visualisation helpers ─────────────────────────────────────────


def draw_annotations(
    image: np.ndarray,
    elements: list[Element],
    color: tuple[int, int, int] = (0, 255, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Draw bounding boxes and index labels on a copy of the image."""
    vis = image.copy()
    for elem in elements:
        x, y, w, h = elem.bbox
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, thickness)
        cv2.putText(
            vis,
            str(elem.index),
            (x, max(y - 4, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return vis


def save_mask_overlay(image: np.ndarray, mask: np.ndarray, path: Path) -> None:
    """Save a side-by-side overlay of original image and mask."""
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        image = image[:, :, :3]

    overlay = image.copy()
    overlay[mask > 0] = (
        overlay[mask > 0] * 0.5 + np.array([0, 255, 0], dtype=np.uint8) * 0.5
    )

    h, w = image.shape[:2]
    combined = np.hstack([image, np.zeros((h, 8, 3), dtype=np.uint8), overlay])
    write_image(path, combined)


# ─── Metadata dumping ───────────────────────────────────────────────


def dump_metadata(
    path: Path,
    source: str,
    bg_type: str,
    bg_options: dict,
    elements: list[Element],
    image_paths: list[str],
) -> None:
    """Write element metadata as JSON."""
    payload = {
        "source": source,
        "bg_type": bg_type,
        "bg_options": bg_options,
        "element_count": len(elements),
        "elements": [
            {**elem.to_dict(), "image": img_path}
            for elem, img_path in zip(elements, image_paths)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote metadata %s", path)
