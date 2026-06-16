"""Background removal: chroma key (green/blue/white/black) + checkerboard.

The chroma implementation is copied from the spritesheet skill's ``chroma.py``
(2026-06-16) so that this skill remains self-contained. If the spritesheet
chroma algorithm changes, copy the updated function here to keep behaviour
in sync.

Also implements:
- Checkerboard background removal (foreground mask from corner hull)
- Auto background-type detection (chroma vs checkerboard)
- Unified ``remove_bg`` entry point
"""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

from detect_checkerboard import detect_checkerboard

logger = logging.getLogger(__name__)


# ─── HSV ranges (copied from spritesheet skill 2026-06-16) ──────────

BG_HSV_RANGES = {
    "green": [
        # Standard green screen: high saturation + medium-high value
        {"h": (30, 90), "s": (80, 255), "v": (80, 255)},
        # Bright green screen (overexposed): lower saturation, high value
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


# ─── Auto background colour detection (copied from spritesheet 2026-06-16) ──


def detect_bg_color(frame: np.ndarray, sample_ratio: float = 0.05) -> str:
    """Sample the four corners of the image and infer the background colour.

    Returns one of: ``green``, ``blue``, ``white``, ``black``.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    margin = int(min(h, w) * sample_ratio)

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

    if mean_s < 30 and mean_v > 200:
        return "white"
    if mean_v < 50:
        return "black"
    if 35 <= mean_h <= 85 and mean_s > 40:
        return "green"
    if 85 < mean_h <= 135 and mean_s > 40:
        return "blue"

    logger.info(
        "Background colour cannot be auto-detected (H=%.0f, S=%.0f, V=%.0f), falling back to white",
        mean_h,
        mean_s,
        mean_v,
    )
    return "white"


# ─── Chroma key background removal (copied from spritesheet 2026-06-16) ──


def remove_chroma_bg(
    frame: np.ndarray,
    bg_color: str = "auto",
) -> Tuple[np.ndarray, np.ndarray]:
    """HSV colour-range segmentation. Returns ``(bgra_image, foreground_mask)``.

    If ``bg_color == "auto"`` the colour is inferred from the four corners.
    """
    if bg_color == "auto":
        bg_color = detect_bg_color(frame)
        logger.info("Auto-detected background colour: %s", bg_color)

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    ranges = BG_HSV_RANGES.get(bg_color, BG_HSV_RANGES["white"])

    # Combine all HSV ranges
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for r in ranges:
        lower = np.array([r["h"][0], r["s"][0], r["v"][0]])
        upper = np.array([r["h"][1], r["s"][1], r["v"][1]])
        partial = cv2.inRange(hsv, lower, upper)
        mask = cv2.bitwise_or(mask, partial)

    # Edge cleanup: large kernel removes noise, small kernel refines
    big_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, big_kernel, iterations=1)
    small_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, small_kernel, iterations=2)

    # Invert: background=0 (transparent), foreground=255
    mask = cv2.bitwise_not(mask)

    # Edge feathering
    mask = cv2.GaussianBlur(mask, (3, 3), 0)

    bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)

    # Green-screen despill: dual-threshold for core vs edge pixels
    if bg_color == "green":
        b, g, r = cv2.split(bgra[:, :, :3])
        g_i = g.astype(int)
        b_i = b.astype(int)
        r_i = r.astype(int)
        spill_core = (g_i > b_i + 5) & (g_i > r_i + 5) & (mask >= 240)
        spill_edge = ((g_i > b_i + 1) | (g_i > r_i + 1)) & (mask > 0) & (mask < 240)
        spill = spill_core | spill_edge
        if spill.any():
            target_g = np.maximum(np.maximum(b_i, r_i), (b_i + r_i) // 2)
            new_g = np.where(spill, target_g, g_i).astype(np.uint8)
            bgra[:, :, 1] = new_g

    bgra[:, :, 3] = mask
    return bgra, mask


# ─── Checkerboard background removal ────────────────────────────────


def remove_checkerboard_bg(
    image: np.ndarray,
    corners: np.ndarray | None = None,
    pattern_size: Tuple[int, int] = (9, 6),
) -> Tuple[np.ndarray, np.ndarray]:
    """Remove a checkerboard background and return ``(bgra, foreground_mask)``.

    Strategy:
      1. Detect the checkerboard corners (or use the provided ones).
      2. Build a convex-hull ROI covering the checkerboard region.
      3. Inside the ROI, classify foreground vs checkerboard using local
         variance (checkerboard has high variance, foreground is smooth).
      4. Outside the ROI, mark pixels as background (0).

    The returned ``mask`` is a foreground mask (255 = foreground, 0 = background).
    """
    h, w = image.shape[:2]
    if corners is None:
        corners, confidence = detect_checkerboard(image, pattern_size)

    if corners is None:
        logger.warning(
            "Checkerboard not detected; falling back to local-variance mask"
        )
        mask = _variance_fallback_mask(image)
    else:
        # Build ROI from the convex hull
        pts = corners.reshape(-1, 2).astype(np.int32)
        hull = cv2.convexHull(pts)
        roi_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillConvexPoly(roi_mask, hull, 255)

        # Inside ROI: detect foreground via HSV saturation
        # (checkerboard is grey/white, UI elements usually have colour)
        sat_fg = _saturation_fallback_mask(image)
        mask = cv2.bitwise_and(sat_fg, roi_mask)

    # Clean up the mask
    mask = _clean_foreground_mask(mask)

    bgra = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    bgra[:, :, 3] = mask
    return bgra, mask


def _hull_mask(shape: tuple, corners: np.ndarray) -> np.ndarray:
    """Build a binary mask covering the detected checkerboard region."""
    h, w = shape[:2]
    pts = corners.reshape(-1, 2).astype(np.int32)
    hull = cv2.convexHull(pts)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, hull, 255)

    # Erode slightly to keep the foreground boundary inside the hull
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.erode(mask, kernel, iterations=1)
    return mask


def _variance_fallback_mask(image: np.ndarray) -> np.ndarray:
    """Build a foreground mask by thresholding local variance.

    Foreground regions typically have lower local variance than the
    checkerboard texture. Used when corner detection fails.
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    g = gray.astype(np.float32)
    mean = cv2.boxFilter(g, -1, (15, 15))
    sqmean = cv2.boxFilter(g * g, -1, (15, 15))
    var = np.maximum(sqmean - mean * mean, 0)
    var_norm = cv2.normalize(var, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(var_norm, 25, 255, cv2.THRESH_BINARY_INV)
    return mask


def _saturation_fallback_mask(image: np.ndarray) -> np.ndarray:
    """Build a foreground mask by HSV saturation.

    Checkerboard (grey/white) has low saturation, coloured UI elements have
    higher saturation. Used as the default foreground detector when checker
    corners are known.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]
    # Threshold saturation: anything > 25 is foreground
    _, mask = cv2.threshold(s, 25, 255, cv2.THRESH_BINARY)
    return mask


def _clean_foreground_mask(mask: np.ndarray) -> np.ndarray:
    """Morphological cleanup of the foreground mask."""
    kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    kernel_large = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_large, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_small, iterations=2)
    # Light blur for soft edges
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    return mask


# ─── Auto background-type detection ────────────────────────────────


def detect_bg_type(image: np.ndarray) -> str:
    """Return ``"chroma"``, ``"checkerboard"`` or ``"unknown"``.

    Heuristic:
    1. Sample four corners. If the variance of corner pixels is high, the
       background is likely a checkerboard pattern.
    2. Otherwise, fall back to chroma auto-detection. If a uniform colour
       (green/blue/white/black) is identified, return ``"chroma"``.
    """
    h, w = image.shape[:2]
    margin = int(min(h, w) * 0.05)

    # Corner sampling
    corner_pixels = np.vstack(
        [
            image[0:margin, 0:margin].reshape(-1, 3),
            image[0:margin, w - margin:w].reshape(-1, 3),
            image[h - margin:h, 0:margin].reshape(-1, 3),
            image[h - margin:h, w - margin:w].reshape(-1, 3),
        ]
    ).astype(np.float32)

    corner_std = corner_pixels.std(axis=0).mean()
    # High std in corner regions usually indicates checkerboard
    if corner_std > 8:
        return "checkerboard"

    # Try checkerboard detection via the dedicated detector — soft-gradient
    # backgrounds may have uniform corners but still be checkerboards.
    try:
        from detect_checkerboard import detect_checkerboard as _detect
        corners, _conf = _detect(image)
        if corners is not None:
            return "checkerboard"
    except Exception:  # pragma: no cover - defensive
        pass

    # Low std and no corners: try chroma auto-detection
    bg = detect_bg_color(image)
    if bg in ("green", "blue", "white", "black"):
        return "chroma"
    return "unknown"


# ─── Unified entry point ────────────────────────────────────────────


def remove_bg(
    image: np.ndarray,
    bg_type: str = "auto",
    bg_color: str = "auto",
    pattern_size: Tuple[int, int] = (9, 6),
) -> Tuple[np.ndarray, np.ndarray, str]:
    """Remove the background and return ``(bgra, mask, used_bg_type)``."""
    if bg_type == "auto":
        detected = detect_bg_type(image)
        logger.info("Auto-detected background type: %s", detected)
        bg_type = detected if detected != "unknown" else "chroma"

    if bg_type == "checkerboard":
        bgra, mask = remove_checkerboard_bg(image, pattern_size=pattern_size)
    elif bg_type == "chroma":
        bgra, mask = remove_chroma_bg(image, bg_color=bg_color)
    else:
        raise ValueError(f"Unknown bg_type: {bg_type}")

    return bgra, mask, bg_type
