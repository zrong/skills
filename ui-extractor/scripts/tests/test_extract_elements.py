"""Tests for UI element extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extract_elements import extract_ui_elements
from remove_bg import remove_chroma_bg


SAMPLES = Path(__file__).resolve().parent.parent.parent / "assets"


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(SAMPLES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"Missing sample: {name}"
    return img


def test_extract_from_green_chroma() -> None:
    img = _load("sample_chroma_green.png")
    bgra, mask = remove_chroma_bg(img, bg_color="green")
    elements = extract_ui_elements(mask, bgra, min_area=200)
    # We drew: 1 big panel + 2 buttons + 3 circles + 1 star + 1 text bar
    # Circles are small but should be kept with min_area=200
    assert len(elements) >= 4
    # Each element should have a valid BGRA crop
    for elem in elements:
        assert elem.image.ndim == 3
        assert elem.image.shape[2] == 4
        assert elem.area > 0
        assert 0 < elem.aspect_ratio <= 10.0


def test_extract_returns_zero_on_blank_mask() -> None:
    img = _load("sample_chroma_green.png")
    blank_mask = np.zeros(img.shape[:2], dtype=np.uint8)
    elements = extract_ui_elements(blank_mask, img, min_area=100)
    assert elements == []


def test_extract_filters_small_components() -> None:
    img = _load("sample_chroma_green.png")
    bgra, mask = remove_chroma_bg(img, bg_color="green")
    elements = extract_ui_elements(mask, bgra, min_area=10_000)
    # Only the top panel and bottom text bar are larger than 10k px²
    assert all(e.area >= 10_000 for e in elements)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
