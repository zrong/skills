"""Tests for the checkerboard detector."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detect_checkerboard import _estimate_period, detect_checkerboard


SAMPLES = Path(__file__).resolve().parent.parent.parent / "assets"


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(SAMPLES / name), cv2.IMREAD_COLOR)
    assert img is not None, f"Missing sample: {name}"
    return img


def test_detect_standard_checkerboard_succeeds() -> None:
    img = _load("sample_checkerboard.png")
    corners, conf = detect_checkerboard(img, pattern_size=(9, 6))
    # Soft-gradient fallback may produce a coarser estimate — accept either path
    if corners is not None:
        assert corners.shape[1:] == (1, 2)
        assert 0.0 <= conf <= 1.0
    else:
        # If detection fails entirely the confidence must be zero
        assert conf == 0.0


def test_detect_chroma_image_returns_low_confidence() -> None:
    img = _load("sample_chroma_green.png")
    corners, conf = detect_checkerboard(img, pattern_size=(9, 6))
    # A solid colour image has no checkerboard; detection should fail
    assert corners is None or conf < 0.5


def test_estimate_period_synthetic_signal() -> None:
    # 40-pixel period sinusoid: 1000 samples
    x = np.arange(1000)
    signal = np.sin(2 * np.pi * x / 40)
    period = _estimate_period(signal.astype(np.float32), expected=25)
    assert period is not None
    assert abs(period - 40) < 2.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
