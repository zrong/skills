from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from imggen.models import ImageArtifact
from imggen.output import output_paths, save_artifacts


def _jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (3, 2), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_explicit_extension_transcodes_artifact(tmp_path: Path) -> None:
    output = tmp_path / "result.png"
    saved = save_artifacts(
        [ImageArtifact(_jpeg(), "image/jpeg")], str(output), None, None, False
    )
    with Image.open(saved[0]) as image:
        assert image.format == "PNG"
        assert image.size == (3, 2)


def test_suffixless_output_gets_extension(tmp_path: Path) -> None:
    assert output_paths(1, str(tmp_path / "result"), None, None)[0].suffix == ".png"
