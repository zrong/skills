from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from matting_cli.config import MattingConfig
from matting_cli.errors import MattingError
from matting_cli.inspection import inspect_image
from matting_cli.selection import select


def _capabilities() -> dict:
    return {
        "methods": {
            "birefnet": {"requires_model": True},
            "birefnet_luma_restore": {"requires_model": True},
            "birefnet_corridorkey_refine": {"requires_model": True},
        },
        "models": [
            "birefnet-auto-quality",
            "birefnet-auto-quality-corridorkey",
        ],
    }


def test_green_screen_selects_live_corridorkey_combo(tmp_path: Path) -> None:
    path = tmp_path / "green.png"
    image = Image.new("RGB", (96, 96), "#00dd22")
    ImageDraw.Draw(image).ellipse((24, 16, 72, 88), fill="#e0a080")
    image.save(path)
    inspected = inspect_image(path, max_input_bytes=1_000_000, max_pixels=1_000_000)
    chosen = select(inspected, MattingConfig("http://example.test"), _capabilities())
    assert inspected["screen_color"] == "green"
    assert chosen.method == "birefnet_corridorkey_refine"
    assert chosen.model == "birefnet-auto-quality-corridorkey"
    assert chosen.parameters["corridorkey_screen"] == "green"


def test_existing_alpha_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
    image.putpixel((0, 0), (255, 0, 0, 0))
    image.save(path)
    inspected = inspect_image(path, max_input_bytes=1_000_000, max_pixels=1_000_000)
    chosen = select(inspected, MattingConfig("http://example.test"), _capabilities())
    assert chosen.action == "preserve_existing_alpha"
    assert chosen.method is None


def test_unavailable_corridorkey_is_filtered_even_if_method_is_listed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "green.png"
    Image.new("RGB", (32, 32), "#00dd22").save(path)
    inspected = inspect_image(path, max_input_bytes=1_000_000, max_pixels=1_000_000)
    capabilities = _capabilities()
    capabilities["corridorkey"] = {"available": False}
    chosen = select(inspected, MattingConfig("http://example.test"), capabilities)
    assert chosen.method == "birefnet"
    assert chosen.model == "birefnet-auto-quality"


def test_explicit_unknown_pair_requires_compatibility_mapping(tmp_path: Path) -> None:
    path = tmp_path / "plain.png"
    Image.new("RGB", (32, 32), "white").save(path)
    inspected = inspect_image(path, max_input_bytes=1_000_000, max_pixels=1_000_000)
    capabilities = {"methods": {"custom": {}}, "models": ["custom-model"]}
    with pytest.raises(MattingError, match="无法证明"):
        select(
            inspected,
            MattingConfig("http://example.test"),
            capabilities,
            method="custom",
            model="custom-model",
        )
