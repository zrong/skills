"""Prompt file handling and the system imagegen structured augmentation format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from imggen.models import ImggenError


PROMPT_FIELDS = (
    "use_case",
    "scene",
    "subject",
    "style",
    "composition",
    "lighting",
    "palette",
    "materials",
    "text",
    "constraints",
    "negative",
)


def read_prompt(
    positional: str | None, option: str | None, prompt_file: str | None
) -> str:
    values = [value for value in (positional, option, prompt_file) if value]
    if len(values) != 1:
        raise ImggenError(
            "必须且只能使用 positional prompt、--prompt 或 --prompt-file 之一"
        )
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if not path.is_file():
            raise ImggenError(f"Prompt 文件不存在: {path}")
        prompt = path.read_text(encoding="utf-8").strip()
    else:
        prompt = str(positional or option).strip()
    if not prompt:
        raise ImggenError("Prompt 不能为空")
    return prompt


def augment_prompt(prompt: str, fields: dict[str, Any], enabled: bool = True) -> str:
    if not enabled:
        return prompt
    labels = {
        "use_case": "Use case",
        "scene": "Scene/background",
        "subject": "Subject",
        "style": "Style/medium",
        "composition": "Composition/framing",
        "lighting": "Lighting/mood",
        "palette": "Color palette",
        "materials": "Materials/textures",
        "text": "Text (verbatim)",
        "constraints": "Constraints",
        "negative": "Avoid",
    }
    sections: list[str] = []
    if fields.get("use_case"):
        sections.append(f"Use case: {fields['use_case']}")
    sections.append(f"Primary request: {prompt}")
    for key in PROMPT_FIELDS[1:]:
        value = fields.get(key)
        if value:
            rendered = f'"{value}"' if key == "text" else value
            sections.append(f"{labels[key]}: {rendered}")
    return "\n".join(sections)
