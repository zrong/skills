"""Seedream 5.0 Pro coordinate markup and recoverable local edit sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from imggen.models import ImggenError


@dataclass(frozen=True)
class Markup:
    points: tuple[tuple[int, int], ...] = ()
    boxes: tuple[tuple[int, int, int, int], ...] = ()


def parse_point(value: str) -> tuple[float, float]:
    values = _numbers(value, 2, "point")
    return values[0], values[1]


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    values = _numbers(value, 4, "bbox")
    return values[0], values[1], values[2], values[3]


def normalize_markup(
    points: list[tuple[float, float]],
    boxes: list[tuple[float, float, float, float]],
    canvas_size: tuple[int, int] | None,
) -> Markup:
    if canvas_size:
        width, height = canvas_size
        if width < 2 or height < 2:
            raise ImggenError("--canvas-size 的宽高必须 >= 2")

        # Official Seedream guide: round(pixel / displayed_axis * 1000), clamped to 0..999.
        def convert(value: float, axis: int) -> int:
            return min(999, max(0, round(value / axis * 1000)))

        normalized_points = tuple(
            (convert(x, width), convert(y, height)) for x, y in points
        )
        normalized_boxes = tuple(
            (
                convert(x1, width),
                convert(y1, height),
                convert(x2, width),
                convert(y2, height),
            )
            for x1, y1, x2, y2 in boxes
        )
    else:
        normalized_points = tuple((round(x), round(y)) for x, y in points)
        normalized_boxes = tuple(
            (round(x1), round(y1), round(x2), round(y2)) for x1, y1, x2, y2 in boxes
        )
    for coordinate in (*normalized_points, *normalized_boxes):
        if any(value < 0 or value > 999 for value in coordinate):
            raise ImggenError("交互坐标归一化后必须全部位于 0..999")
    for x1, y1, x2, y2 in normalized_boxes:
        if x1 >= x2 or y1 >= y2:
            raise ImggenError("bbox 必须满足 x1 < x2 且 y1 < y2")
    return Markup(normalized_points, normalized_boxes)


def marked_prompt(prompt: str, markup: Markup) -> str:
    markers = [f"<point>{x} {y}</point>" for x, y in markup.points]
    markers.extend(
        f"<bbox>{x1} {y1} {x2} {y2}</bbox>" for x1, y1, x2, y2 in markup.boxes
    )
    return f"编辑位置：{' '.join(markers)}\n编辑指令：{prompt}" if markers else prompt


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def create(
        self, provider: str, endpoint: str, model: str, reference: Path
    ) -> dict[str, Any]:
        if self.path.exists():
            raise ImggenError(f"会话已存在: {self.path}")
        now = _now()
        data = {
            "version": 1,
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "initial_reference": str(reference.resolve()),
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }
        self.save(data)
        return data

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise ImggenError(f"会话文件不存在: {self.path}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ImggenError(f"会话文件损坏: {self.path}: {exc}") from exc
        if data.get("version") != 1 or not isinstance(data.get("turns"), list):
            raise ImggenError(f"不支持的会话格式: {self.path}")
        return data

    def save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temp.replace(self.path)

    @staticmethod
    def latest_reference(data: dict[str, Any]) -> Path:
        completed = [
            turn for turn in data["turns"] if turn.get("status") == "completed"
        ]
        value = completed[-1]["outputs"][0] if completed else data["initial_reference"]
        path = Path(value)
        if not path.is_file():
            raise ImggenError(f"会话引用产物不存在，无法恢复: {path}")
        return path

    def begin_turn(
        self,
        data: dict[str, Any],
        prompt: str,
        rendered_prompt: str,
        reference: Path,
        markup: Markup,
        request_options: dict[str, Any],
    ) -> int:
        turn = {
            "index": len(data["turns"]) + 1,
            "status": "pending",
            "prompt": prompt,
            "rendered_prompt": rendered_prompt,
            "reference": str(reference.resolve()),
            "points": [list(value) for value in markup.points],
            "boxes": [list(value) for value in markup.boxes],
            "request_options": request_options,
            "started_at": _now(),
        }
        data["turns"].append(turn)
        self.save(data)
        return len(data["turns"]) - 1

    def finish_turn(
        self, data: dict[str, Any], index: int, outputs: list[Path]
    ) -> None:
        data["turns"][index].update(
            {
                "status": "completed",
                "outputs": [str(path.resolve()) for path in outputs],
                "finished_at": _now(),
            }
        )
        self.save(data)

    def fail_turn(self, data: dict[str, Any], index: int, error: Exception) -> None:
        data["turns"][index].update(
            {"status": "failed", "error": str(error), "finished_at": _now()}
        )
        self.save(data)


def parse_canvas_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    parts = value.lower().split("x", 1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ImggenError("--canvas-size 格式必须是 WIDTHxHEIGHT")
    return int(parts[0]), int(parts[1])


def _numbers(value: str, count: int, label: str) -> tuple[float, ...]:
    try:
        values = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise ImggenError(f"--{label} 必须是逗号分隔的数字") from exc
    if len(values) != count:
        raise ImggenError(f"--{label} 需要 {count} 个逗号分隔数字")
    return values


def _now() -> str:
    return datetime.now(UTC).isoformat()
