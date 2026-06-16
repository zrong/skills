"""Perspective correction using a detected checkerboard.

Given corner positions produced by ``detect_checkerboard``, build a
perspective transform that maps the four outer corners to a rectangle
aligned with the axes. The output dimensions are derived from the four
sides of the source quadrilateral.
"""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def warp_perspective(
    image: np.ndarray,
    corners: np.ndarray,
    pattern_size: Tuple[int, int],
) -> np.ndarray:
    """Apply a perspective transform that flattens the checkerboard plane.

    Args:
        image: BGR source image.
        corners: Corner positions from ``detect_checkerboard``. Shape
            ``(cols*rows, 1, 2)`` arranged in row-major order.
        pattern_size: ``(cols, rows)`` matching the inner-corner grid.

    Returns:
        The warped BGR image.
    """
    if corners is None or len(corners) < 4:
        raise ValueError("Need at least four corner points to warp")

    cols, rows = pattern_size
    grid = corners.reshape(rows, cols, 2)

    src_pts = np.array(
        [
            grid[0, 0],  # top-left
            grid[0, -1],  # top-right
            grid[-1, -1],  # bottom-right
            grid[-1, 0],  # bottom-left
        ],
        dtype=np.float32,
    )

    width_top = np.linalg.norm(src_pts[0] - src_pts[1])
    width_bottom = np.linalg.norm(src_pts[3] - src_pts[2])
    height_left = np.linalg.norm(src_pts[0] - src_pts[3])
    height_right = np.linalg.norm(src_pts[1] - src_pts[2])

    max_w = int(max(width_top, width_bottom))
    max_h = int(max(height_left, height_right))

    dst_pts = np.array(
        [
            [0, 0],
            [max_w - 1, 0],
            [max_w - 1, max_h - 1],
            [0, max_h - 1],
        ],
        dtype=np.float32,
    )

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(
        image,
        M,
        (max_w, max_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    logger.info("Warped to %dx%d", max_w, max_h)
    return warped
