"""Portable discovery and loading helpers for ``agent_config.toml``.

Copied from ``shared/agent-config`` so the skill remains independently
installable.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

CONFIG_FILENAME = "agent_config.toml"
DEFAULT_GLOBAL_CONFIG = Path.home() / ".agents" / CONFIG_FILENAME
MissingPolicy = Literal["empty", "raise"]


class ConfigNotFoundError(FileNotFoundError):
    def __init__(self, searched_paths: tuple[Path, ...]):
        self.searched_paths = searched_paths
        locations = ", ".join(str(path) for path in searched_paths)
        super().__init__(f"No configuration file found. Looked at: {locations}")


def _absolute(path: str | Path, *, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def find_git_root(start: str | Path) -> Path | None:
    current = Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def config_candidates(
    skill_dir: str | Path,
    *,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
    filename: str = CONFIG_FILENAME,
) -> tuple[Path, ...]:
    working_dir = _absolute(cwd or Path.cwd(), base=Path.cwd())
    resolved_skill_dir = _absolute(skill_dir, base=working_dir)
    resolved_global_config = _absolute(
        global_config or DEFAULT_GLOBAL_CONFIG,
        base=working_dir,
    )
    candidates = [working_dir / filename, resolved_skill_dir / filename]
    git_root = find_git_root(working_dir)
    if git_root is not None:
        candidates.append(git_root / filename)
    candidates.append(resolved_global_config)
    return tuple(dict.fromkeys(path.resolve() for path in candidates))


def find_config(
    skill_dir: str | Path,
    *,
    path: str | Path | None = None,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
    filename: str = CONFIG_FILENAME,
) -> Path:
    working_dir = _absolute(cwd or Path.cwd(), base=Path.cwd())
    if path is not None:
        candidates = (_absolute(path, base=working_dir),)
    else:
        candidates = config_candidates(
            skill_dir,
            cwd=working_dir,
            global_config=global_config,
            filename=filename,
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
    filename: str = CONFIG_FILENAME,
    missing: MissingPolicy = "empty",
) -> tuple[dict, Path | None]:
    if missing not in {"empty", "raise"}:
        raise ValueError("missing must be 'empty' or 'raise'")
    try:
        config_path = find_config(
            skill_dir,
            path=path,
            cwd=cwd,
            global_config=global_config,
            filename=filename,
        )
    except ConfigNotFoundError:
        if path is None and missing == "empty":
            return {}, None
        raise
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return config, config_path


def load_section(
    section: str, skill_dir: str | Path, **kwargs
) -> tuple[dict, Path | None]:
    config, config_path = load_config(skill_dir, **kwargs)
    value = config.get(section, {})
    if not isinstance(value, dict):
        raise TypeError(f"Configuration section [{section}] must be a TOML table")
    return value, config_path
