"""Output path construction, collision protection, and optional downscaling."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from imggen.models import ImageArtifact, ImggenError


def extension_for(mime_type: str, requested: str | None) -> str:
    if requested:
        value = requested.lower()
        return "jpg" if value in {"jpg", "jpeg"} else value
    return {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "png")


def output_paths(
    count: int,
    output: str | None,
    out_dir: str | None,
    output_format: str | None,
    artifacts: list[ImageArtifact] | None = None,
) -> list[Path]:
    if output and out_dir:
        raise ImggenError("--out/--output 与 --out-dir 不能同时使用")
    mime = artifacts[0].mime_type if artifacts else "image/png"
    extension = extension_for(mime, output_format)
    if out_dir:
        directory = Path(out_dir).expanduser()
        return [
            directory / f"generated_{index + 1}.{extension}" for index in range(count)
        ]
    base = Path(output or f"generated.{extension}").expanduser()
    if not base.suffix:
        base = base.with_suffix(f".{extension}")
    if count == 1:
        return [base]
    suffix = base.suffix or f".{extension}"
    stem = base.stem if base.suffix else base.name
    return [base.with_name(f"{stem}_{index + 1}{suffix}") for index in range(count)]


def save_artifacts(
    artifacts: list[ImageArtifact],
    output: str | None,
    out_dir: str | None,
    output_format: str | None,
    force: bool,
    downscale_max_dim: int | None = None,
    downscale_suffix: str = "-small",
) -> list[Path]:
    paths = output_paths(len(artifacts), output, out_dir, output_format, artifacts)
    _check_collisions(paths, force, downscale_max_dim, downscale_suffix)
    written: list[Path] = []
    for artifact, path in zip(artifacts, paths, strict=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_encode_for_path(artifact.data, path))
        written.append(path.resolve())
        if downscale_max_dim is not None:
            derived = _derived_path(path, downscale_suffix)
            derived.write_bytes(
                _downscale(artifact.data, downscale_max_dim, path.suffix)
            )
            written.append(derived.resolve())
    return written


def preflight_outputs(
    count: int,
    output: str | None,
    out_dir: str | None,
    output_format: str | None,
    force: bool,
    downscale_max_dim: int | None = None,
    downscale_suffix: str = "-small",
) -> list[Path]:
    paths = output_paths(count, output, out_dir, output_format)
    _check_collisions(paths, force, downscale_max_dim, downscale_suffix)
    return paths


def _check_collisions(
    paths: list[Path], force: bool, downscale_max_dim: int | None, downscale_suffix: str
) -> None:
    if force:
        return
    candidates = list(paths)
    if downscale_max_dim is not None:
        candidates.extend(_derived_path(path, downscale_suffix) for path in paths)
    existing = [path for path in candidates if path.exists()]
    if existing:
        raise ImggenError(f"输出已存在: {existing[0]}（使用 --force 覆盖）")


def _derived_path(path: Path, suffix: str) -> Path:
    rendered = suffix if suffix.startswith(("-", "_")) else f"-{suffix}"
    return path.with_name(f"{path.stem}{rendered}{path.suffix}")


def _downscale(raw: bytes, max_dim: int, suffix: str) -> bytes:
    if max_dim < 1:
        raise ImggenError("--downscale-max-dim 必须 >= 1")
    with Image.open(BytesIO(raw)) as image:
        image.load()
        image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        target = BytesIO()
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(
            suffix.lower(), "PNG"
        )
        if fmt == "JPEG" and image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image.save(target, format=fmt)
        return target.getvalue()


def _encode_for_path(raw: bytes, path: Path) -> bytes:
    target_format = {
        ".png": "PNG",
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".webp": "WEBP",
    }.get(path.suffix.lower())
    if target_format is None:
        return raw
    with Image.open(BytesIO(raw)) as image:
        image.load()
        if image.format == target_format:
            return raw
        if target_format == "JPEG" and image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        out = BytesIO()
        image.save(out, format=target_format)
        return out.getvalue()
