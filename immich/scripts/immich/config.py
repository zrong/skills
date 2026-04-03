"""Configuration loader for immich skill.

Borrowed the config lookup pattern from rspeak skill.
"""

import tomllib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILENAME = "agent_config.toml"

_config_cache: dict | None = None


def _find_config() -> tuple[Path, Path]:
    """Find config file by priority: CWD > skill_dir > up to .git parent."""
    candidates = [
        Path.cwd() / CONFIG_FILENAME,
        SKILL_DIR / CONFIG_FILENAME,
    ]
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            candidates.append(parent / CONFIG_FILENAME)
            break

    for candidate in candidates:
        if candidate.exists():
            return candidate, SKILL_DIR

    raise FileNotFoundError(f"No config file found. Looked at: {candidates}")


def load_config(path: Path | None = None) -> dict:
    """Load the full config.toml file."""
    global _config_cache
    if path is None:
        try:
            path, _ = _find_config()
        except FileNotFoundError:
            return {}
    _config_cache = tomllib.loads(path.read_text(encoding="utf-8"))
    return _config_cache


def get_immich_config(config: dict | None = None) -> dict:
    """Get the [immich] section of the config."""
    if config is None:
        config = _config_cache or load_config()
    return config.get("immich", {})


def get_base_url(config: dict | None = None) -> str:
    """Get Immich server base URL."""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("base_url", "").rstrip("/")


def get_api_key(config: dict | None = None) -> str:
    """Get Immich API key."""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("api_key", "")


def get_default_album(config: dict | None = None) -> str | None:
    """Get default album name from config."""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("default_album") or None
