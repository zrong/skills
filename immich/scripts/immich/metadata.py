"""Read video-downloader metadata sidecars and format Immich descriptions."""

from __future__ import annotations

import json
from pathlib import Path


VIDEO_METADATA_SCHEMA = "video-downloader.metadata/v1"
VIDEO_METADATA_SUFFIX = ".metadata.json"


def metadata_sidecar_path(media_path: Path) -> Path:
    return Path(f"{media_path}{VIDEO_METADATA_SUFFIX}")


def load_video_metadata(
    media_path: Path,
    metadata_path: Path | None = None,
) -> dict | None:
    """Load an explicit or adjacent video metadata sidecar."""
    path = metadata_path or metadata_sidecar_path(media_path)
    if not path.exists():
        if metadata_path is None:
            return None
        raise FileNotFoundError(f"Metadata file does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Metadata file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Metadata file must contain a JSON object: {path}")
    if payload.get("schema") != VIDEO_METADATA_SCHEMA:
        raise ValueError(
            f"Unsupported metadata schema in {path}: {payload.get('schema')!r}"
        )
    return payload


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _format_duration(value: object) -> str:
    try:
        total_seconds = max(0, round(float(value)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_tags(value: object) -> str:
    if not isinstance(value, list):
        return ""
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = _text(item).lstrip("#")
        if tag and tag not in seen:
            tags.append(f"#{tag}")
            seen.add(tag)
    return " ".join(tags)


def format_video_description(metadata: dict) -> str:
    """Build a readable Immich description from available public metadata."""
    title = _text(metadata.get("title"))
    original_description = _text(metadata.get("description"))
    summary_fields = (
        ("标题", title),
        ("作者", _text(metadata.get("author_name"))),
        ("作者 ID", _text(metadata.get("author_id"))),
        ("平台", _text(metadata.get("platform") or metadata.get("backend"))),
        ("发布时间", _text(metadata.get("published_at"))),
        ("时长", _format_duration(metadata.get("duration_seconds"))),
        ("视频 ID", _text(metadata.get("media_id"))),
    )
    sections = [
        "\n".join(f"{label}：{value}" for label, value in summary_fields if value)
    ]
    if original_description and original_description != title:
        sections.append(f"原始描述：\n{original_description}")
    tags = _format_tags(metadata.get("tags"))
    if tags:
        sections.append(f"话题：{tags}")
    source_url = _text(metadata.get("source_url"))
    if source_url:
        sections.append(f"来源：{source_url}")
    return "\n\n".join(section for section in sections if section)
