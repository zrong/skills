"""Strict Python 3.13 agent_config.toml discovery helper."""

from __future__ import annotations

import tomllib
from datetime import date, datetime, time
from pathlib import Path
from typing import cast

type TomlScalar = str | int | float | bool | datetime | date | time
type TomlValue = TomlScalar | list[TomlValue] | dict[str, TomlValue]
type TomlTable = dict[str, TomlValue]

CONFIG_FILENAME = "agent_config.toml"


class ConfigNotFoundError(FileNotFoundError):
    """Raised when no agent configuration exists in the lookup chain."""

    def __init__(self, searched_paths: tuple[Path, ...]) -> None:
        self.searched_paths = searched_paths
        locations = ", ".join(str(path) for path in searched_paths)
        super().__init__(f"No configuration file found. Looked at: {locations}")


def _absolute(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path).expanduser()
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _git_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def config_candidates(
    skill_dir: str | Path,
    *,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
) -> tuple[Path, ...]:
    working_dir = _absolute(cwd or Path.cwd(), base=Path.cwd())
    resolved_skill_dir = _absolute(skill_dir, base=working_dir)
    resolved_global = _absolute(
        global_config or Path.home() / ".agents" / CONFIG_FILENAME,
        base=working_dir,
    )
    candidates = [
        working_dir / CONFIG_FILENAME,
        resolved_skill_dir / CONFIG_FILENAME,
    ]
    if git_root := _git_root(working_dir):
        candidates.append(git_root / CONFIG_FILENAME)
    candidates.append(resolved_global)
    return tuple(dict.fromkeys(path.resolve() for path in candidates))


def find_config(
    skill_dir: str | Path,
    *,
    path: str | Path | None = None,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
) -> Path:
    working_dir = _absolute(cwd or Path.cwd(), base=Path.cwd())
    candidates = (
        (_absolute(path, base=working_dir),)
        if path is not None
        else config_candidates(skill_dir, cwd=working_dir, global_config=global_config)
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigNotFoundError(candidates)


def load_config(
    skill_dir: str | Path,
    *,
    path: str | Path | None = None,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
) -> tuple[TomlTable, Path]:
    config_path = find_config(
        skill_dir,
        path=path,
        cwd=cwd,
        global_config=global_config,
    )
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return cast(TomlTable, parsed), config_path
