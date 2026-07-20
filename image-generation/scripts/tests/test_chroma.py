from __future__ import annotations

from pathlib import Path

from PIL import Image

from imggen.chroma import remove_chroma_key


def test_chroma_key_removes_background(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "output.png"
    image = Image.new("RGB", (4, 4), "#00ff00")
    image.putpixel((2, 2), (255, 0, 0))
    image.save(source)
    result = remove_chroma_key(str(source), str(output))
    with Image.open(output) as rendered:
        assert rendered.getpixel((0, 0))[3] == 0
        assert rendered.getpixel((2, 2))[3] == 255
    assert result["transparent_pixels"] == 15
