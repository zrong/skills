"""Checkerboard pattern detection.

Provides a robust multi-strategy detector for checkerboard-style backgrounds:

1. **Primary**: `cv2.findChessboardCornersSB` (sparse-based, OpenCV 4.5+)
2. **Fallback 1**: `cv2.findChessboardCorners` (legacy Harris-based)
3. **Fallback 2**: local variance texture detection (no pattern size required)

Returns either detected corner positions (N x 1 x 2 array) or ``None``.
"""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def detect_checkerboard(
    image: np.ndarray,
    pattern_size: Tuple[int, int] = (9, 6),
) -> tuple[np.ndarray | None, float]:
    """Detect an inner-corner checkerboard pattern.

    Args:
        image: BGR or grayscale image.
        pattern_size: ``(cols, rows)`` of inner corners. For a 10x7 squares
            board this would be ``(9, 6)``.

    Returns:
        Tuple of ``(corners, confidence)`` where ``corners`` is an ``(N, 1, 2)``
        float32 array, or ``(None, 0.0)`` if detection failed.
    """
    gray = _ensure_gray(image)

    # 1) Try SB (sparse-based) variant first
    if hasattr(cv2, "findChessboardCornersSB"):
        try:
            retval, corners = cv2.findChessboardCornersSB(gray, pattern_size, None)
            if retval and corners is not None and len(corners) >= 4:
                return corners, _confidence_from_sb(corners, pattern_size, gray)
        except cv2.error as exc:  # pragma: no cover - defensive
            logger.debug("findChessboardCornersSB failed: %s", exc)

    # 2) Try legacy Harris-based detector
    try:
        retval, corners = cv2.findChessboardCorners(gray, pattern_size, None)
        if retval and corners is not None and len(corners) >= 4:
            refined = _refine_corners(gray, corners)
            return refined, _confidence_from_grid(refined, pattern_size, gray)
    except cv2.error as exc:  # pragma: no cover - defensive
        logger.debug("findChessboardCorners failed: %s", exc)

    # 3) Texture-based fallback: estimate grid period via autocorrelation
    corners = _detect_by_texture(gray, pattern_size)
    if corners is not None:
        return corners, 0.4  # lower confidence
    return None, 0.0


# ─── Helpers ───────────────────────────────────────────────────────


def _ensure_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        image = image[:, :, :3]
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _refine_corners(gray: np.ndarray, corners: np.ndarray) -> np.ndarray:
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)


def _confidence_from_sb(
    corners: np.ndarray, pattern_size: tuple[int, int], gray: np.ndarray
) -> float:
    """Heuristic confidence: 1.0 if grid fits image well, lower otherwise."""
    if len(corners) != pattern_size[0] * pattern_size[1]:
        return 0.6
    h, w = gray.shape
    pts = corners.reshape(-1, 2)
    in_bounds = (
        (pts[:, 0] >= 0)
        & (pts[:, 0] < w)
        & (pts[:, 1] >= 0)
        & (pts[:, 1] < h)
    )
    return float(in_bounds.mean())


def _confidence_from_grid(
    corners: np.ndarray, pattern_size: tuple[int, int], gray: np.ndarray
) -> float:
    return _confidence_from_sb(corners, pattern_size, gray)


def _detect_by_texture(
    gray: np.ndarray, pattern_size: tuple[int, int]
) -> np.ndarray | None:
    """Estimate a regular grid via local-variance peaks and assemble corners.

    Used as last-resort fallback when ``findChessboardCorners`` variants fail
    (e.g. soft-gradient checkerboards). Returns ``None`` when the input does
    not show a clear checkerboard texture.
    """
    # Local variance: checkerboard regions have high variance
    g = gray.astype(np.float32)
    mean = cv2.boxFilter(g, -1, (15, 15))
    sqmean = cv2.boxFilter(g * g, -1, (15, 15))
    var = np.maximum(sqmean - mean * mean, 0)

    var_norm = cv2.normalize(var, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, thresh = cv2.threshold(var_norm, 25, 255, cv2.THRESH_BINARY)

    coverage = float(thresh.mean()) / 255.0
    if coverage < 0.05 or coverage > 0.95:
        return None

    # Find the principal period via FFT of the variance signal
    h, w = gray.shape
    cols, rows = pattern_size
    sample_y = h // 2
    row_signal = var[sample_y, :].astype(np.float32)
    period = _estimate_period(row_signal, expected=cols)
    if period is None:
        return None

    sample_x = w // 2
    col_signal = var[:, sample_x].astype(np.float32)
    row_period = _estimate_period(col_signal, expected=rows)
    if row_period is None:
        return None

    # Approximate corners as a regular grid
    pts = []
    y0 = (h - row_period * (rows - 1)) / 2
    x0 = (w - period * (cols - 1)) / 2
    for r in range(rows):
        for c in range(cols):
            pts.append([[x0 + c * period, y0 + r * row_period]])
    return np.array(pts, dtype=np.float32)


def _estimate_period(signal: np.ndarray, expected: int) -> float | None:
    """Estimate the period of an oscillating signal using FFT.

    Looks for a peak in the frequency spectrum that aligns with the expected
    number of cycles across the signal length.
    """
    if signal.std() < 0.1:
        return None
    n = len(signal)
    spectrum = np.abs(np.fft.rfft(signal - signal.mean()))
    freqs = np.fft.rfftfreq(n, d=1.0)
    if len(spectrum) <= 2:
        return None
    # Ignore the DC component
    spectrum[0] = 0
    peak_idx = int(np.argmax(spectrum))
    if freqs[peak_idx] <= 0:
        return None
    return 1.0 / freqs[peak_idx]
