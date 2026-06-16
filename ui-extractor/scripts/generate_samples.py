"""Generate sample test images for ui-extractor.

Outputs four PNGs into the assets directory:
- sample_checkerboard.png  (10x7 squares soft-gradient checkerboard with UI)
- sample_chroma_green.png  (solid green background with UI)
- sample_chroma_blue.png   (solid blue background with UI)
- sample_chroma_white.png  (white background with UI)
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _checkerboard_bg(width: int, height: int, cell: int = 40) -> np.ndarray:
    """Generate a soft-gradient checkerboard (Photoshop transparent indicator)."""
    bg = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            colour = 220 if ((x // cell + y // cell) % 2 == 0) else 180
            bg[y:y + cell, x:x + cell] = colour
    # Soften with a Gaussian for "soft gradient" look
    bg = cv2.GaussianBlur(bg, (5, 5), 0)
    return bg


def _solid_bg(width: int, height: int, bgr: tuple[int, int, int]) -> np.ndarray:
    return np.full((height, width, 3), bgr, dtype=np.uint8)


def _draw_ui(canvas: np.ndarray, offset: tuple[int, int] = (0, 0)) -> None:
    """Draw several UI components (rectangles, circles, stars)."""
    ox, oy = offset
    # Big panel
    cv2.rectangle(canvas, (ox + 20, oy + 20), (ox + 360, oy + 80), (40, 90, 200), -1)
    cv2.rectangle(canvas, (ox + 20, oy + 20), (ox + 360, oy + 80), (255, 255, 255), 2)

    # Two buttons
    cv2.rectangle(canvas, (ox + 40, oy + 100), (ox + 160, oy + 150), (60, 60, 60), -1)
    cv2.rectangle(canvas, (ox + 40, oy + 100), (ox + 160, oy + 150), (255, 255, 255), 2)
    cv2.rectangle(canvas, (ox + 200, oy + 100), (ox + 320, oy + 150), (60, 60, 60), -1)
    cv2.rectangle(canvas, (ox + 200, oy + 100), (ox + 320, oy + 150), (255, 255, 255), 2)

    # Three circles
    for i, cx in enumerate((60, 120, 180)):
        cv2.circle(canvas, (ox + cx, oy + 200), 18, (0, 200 - i * 50, 200), -1)
        cv2.circle(canvas, (ox + cx, oy + 200), 18, (255, 255, 255), 2)

    # Star (5-point)
    _draw_star(canvas, (ox + 280, oy + 200), 25, (0, 220, 255), 2)

    # Bottom text bar
    cv2.rectangle(canvas, (ox + 20, oy + 250), (ox + 360, oy + 290), (100, 100, 100), -1)
    cv2.rectangle(canvas, (ox + 20, oy + 250), (ox + 360, oy + 290), (255, 255, 255), 2)


def _draw_star(
    img: np.ndarray,
    centre: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    cx, cy = centre
    pts = []
    for i in range(10):
        angle = -np.pi / 2 + i * np.pi / 5
        r = radius if i % 2 == 0 else radius // 2
        x = int(cx + r * np.cos(angle))
        y = int(cy + r * np.sin(angle))
        pts.append([x, y])
    cv2.polylines(img, [np.array(pts, dtype=np.int32)], True, color, thickness)


def make_checkerboard() -> None:
    bg = _checkerboard_bg(400, 320, cell=40)
    _draw_ui(bg)
    out = ASSETS_DIR / "sample_checkerboard.png"
    cv2.imwrite(str(out), bg)
    print(f"Wrote {out}")


def make_chroma(bgr: tuple[int, int, int], name: str) -> None:
    bg = _solid_bg(400, 320, bgr)
    _draw_ui(bg)
    out = ASSETS_DIR / f"sample_chroma_{name}.png"
    cv2.imwrite(str(out), bg)
    print(f"Wrote {out}")


def main() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    make_checkerboard()
    make_chroma((0, 177, 64), "green")  # standard green-screen BGR
    make_chroma((187, 71, 0), "blue")   # standard blue-screen BGR
    make_chroma((255, 255, 255), "white")
    return 0


if __name__ == "__main__":
    sys.exit(main())
