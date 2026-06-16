"""Tests for background removal (chroma and checkerboard)."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from remove_bg import (
    detect_bg_color,
    detect_bg_type,
    remove_bg,
    remove_checkerboard_bg,
    remove_chroma_bg,
)


SAMPLES = Path(__file__).resolve().parent.parent.parent / "assets"


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(SAMPLES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"Missing sample: {name}"
    return img


def test_chroma_green_detects_green() -> None:
    img = _load("sample_chroma_green.png")
    assert detect_bg_color(img) == "green"


def test_chroma_blue_detects_blue() -> None:
    img = _load("sample_chroma_blue.png")
    assert detect_bg_color(img) == "blue"


def test_chroma_white_detects_white() -> None:
    img = _load("sample_chroma_white.png")
    assert detect_bg_color(img) == "white"


def test_chroma_bg_outputs_bgra_and_mask() -> None:
    img = _load("sample_chroma_green.png")
    bgra, mask = remove_chroma_bg(img, bg_color="green")
    assert bgra.shape[2] == 4
    assert bgra.shape[:2] == img.shape[:2]
    assert mask.shape == img.shape[:2]
    assert mask.dtype == np.uint8
    # Background corners should mostly be transparent
    assert mask[0:5, 0:5].mean() < 30


def test_checkerboard_bg_outputs_bgra_and_mask() -> None:
    img = _load("sample_checkerboard.png")
    bgra, mask = remove_checkerboard_bg(img, pattern_size=(9, 6))
    assert bgra.shape[2] == 4
    assert bgra.shape[:2] == img.shape[:2]
    assert mask.shape == img.shape[:2]
    assert mask.dtype == np.uint8


def test_auto_bg_type_picks_chroma_for_green() -> None:
    img = _load("sample_chroma_green.png")
    assert detect_bg_type(img) == "chroma"


def test_auto_bg_type_picks_checkerboard() -> None:
    img = _load("sample_checkerboard.png")
    assert detect_bg_type(img) == "checkerboard"


def test_auto_bg_type_picks_chroma_for_blue() -> None:
    img = _load("sample_chroma_blue.png")
    assert detect_bg_type(img) == "chroma"


def test_auto_bg_type_picks_chroma_for_white() -> None:
    img = _load("sample_chroma_white.png")
    assert detect_bg_type(img) == "chroma"


def test_unified_remove_bg_chroma_path() -> None:
    img = _load("sample_chroma_green.png")
    bgra, mask, used = remove_bg(img, bg_type="chroma", bg_color="green")
    assert used == "chroma"
    assert bgra.shape[2] == 4


def test_unified_remove_bg_checkerboard_path() -> None:
    img = _load("sample_checkerboard.png")
    bgra, mask, used = remove_bg(img, bg_type="checkerboard", pattern_size=(9, 6))
    assert used == "checkerboard"
    assert bgra.shape[2] == 4


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
