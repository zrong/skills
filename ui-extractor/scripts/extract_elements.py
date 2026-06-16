"""UI element extraction from a foreground mask.

Given a binary foreground mask and the original image, find the connected
foreground components, filter them by area / aspect / circularity, and crop
each into its own BGRA PNG.
"""

from __future__ import annotations

import logging
from typing import List

import cv2
import numpy as np

from utils import (
    Element,
    contour_area,
    contour_aspect_ratio,
    contour_bbox,
    contour_circularity,
    filter_contours,
)

logger = logging.getLogger(__name__)


def extract_ui_elements(
    mask: np.ndarray,
    original: np.ndarray,
    min_area: int = 500,
    max_aspect: float = 10.0,
    padding: int = 2,
) -> List[Element]:
    """Separate independent UI components from a binary foreground mask.

    Args:
        mask: Single-channel uint8 mask where 255 = foreground, 0 = background.
        original: BGR or BGRA source image.
        min_area: Discard components with area below this value (pixels²).
        max_aspect: Discard components whose bbox aspect ratio exceeds this.
        padding: Pixel padding around each crop.

    Returns:
        List of ``Element`` objects sorted left-to-right, top-to-bottom.
    """
    if mask.shape[:2] != original.shape[:2]:
        raise ValueError(
            f"Mask shape {mask.shape[:2]} does not match image {original.shape[:2]}"
        )

    # Threshold & clean
    binary = (mask > 127).astype(np.uint8) * 255

    # External contours = individual components
    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    logger.info("Detected %d raw contours", len(contours))

    valid = filter_contours(contours, min_area=min_area, max_aspect=max_aspect)
    logger.info("After filtering: %d elements", len(valid))

    if not valid:
        return []

    # Sort: top-to-bottom, then left-to-right
    valid = sorted(valid, key=lambda c: (cv2.boundingRect(c)[1], cv2.boundingRect(c)[0]))

    h, w = original.shape[:2]
    elements: List[Element] = []
    for idx, cnt in enumerate(valid, start=1):
        x, y, cw, ch = contour_bbox(cnt)
        area = int(contour_area(cnt))
        aspect = float(contour_aspect_ratio(cnt))
        circularity = float(contour_circularity(cnt))

        # Apply padding
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(w, x + cw + padding)
        y2 = min(h, y + ch + padding)

        crop = original[y1:y2, x1:x2].copy()
        if crop.ndim == 3 and crop.shape[2] == 3:
            # Attach alpha derived from the mask
            alpha = mask[y1:y2, x1:x2]
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
            crop[:, :, 3] = alpha

        elements.append(
            Element(
                index=idx,
                bbox=(int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                area=area,
                aspect_ratio=aspect,
                circularity=circularity,
                image=crop,
            )
        )

    return elements
