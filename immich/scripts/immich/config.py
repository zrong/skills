"""Configuration loader for immich skill.

Borrowed the config lookup pattern from rspeak skill.
"""

import tomllib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILENAME = "agent_config.toml"
GLOBAL_CONFIG = Path.home() / ".agents" / CONFIG_FILENAME

_config_cache: dict | None = None


def _find_config() -> tuple[Path, Path]:
    """Find config by priority: CWD > skill dir > Git root > global config."""
    candidates = [
        Path.cwd() / CONFIG_FILENAME,
        SKILL_DIR / CONFIG_FILENAME,
    ]
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            candidates.append(parent / CONFIG_FILENAME)
            break
    candidates.append(GLOBAL_CONFIG)

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


def normalize_base_url(base_url: str) -> str:
    """Normalize an Immich server URL to exclude the API suffix."""
    base_url = base_url.rstrip("/")
    if base_url.endswith("/api"):
        base_url = base_url[:-4]
    return base_url


def get_base_url(config: dict | None = None) -> str:
    """Get Immich server URL without the API suffix."""
    immich_cfg = get_immich_config(config)
    return normalize_base_url(immich_cfg.get("base_url", ""))


def get_api_key(config: dict | None = None) -> str:
    """Get Immich API key."""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("api_key", "")


def get_default_album(config: dict | None = None) -> str | None:
    """Get default album name from config."""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("default_album") or None
