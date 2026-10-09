"""immich skill 的配置加载器。

查找行为与 ``shared/agent-config`` 保持一致；这份拷贝留在 skill 内，
保证 skill 可独立安装、独立使用。
"""

import tomllib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILENAME = "agent_config.toml"
GLOBAL_CONFIG = Path.home() / ".agents" / CONFIG_FILENAME
ASSET_TIME_SOURCES = frozenset({"upload", "source"})

_config_cache: dict | None = None


def _find_config() -> tuple[Path, Path]:
    """按优先级查找配置：当前目录 > skill 目录 > Git 根 > 全局配置。"""
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
    """加载完整的 config.toml。"""
    global _config_cache
    if path is None:
        try:
            path, _ = _find_config()
        except FileNotFoundError:
            return {}
    _config_cache = tomllib.loads(path.read_text(encoding="utf-8"))
    return _config_cache


def get_immich_config(config: dict | None = None) -> dict:
    """取配置的 [immich] 段。"""
    if config is None:
        config = _config_cache or load_config()
    return config.get("immich", {})


def normalize_base_url(base_url: str) -> str:
    """规范化服务器 URL，去掉 API 后缀。"""
    base_url = base_url.rstrip("/")
    if base_url.endswith("/api"):
        base_url = base_url[:-4]
    return base_url


def get_base_url(config: dict | None = None) -> str:
    """取不带 API 后缀的服务器 URL。"""
    immich_cfg = get_immich_config(config)
    return normalize_base_url(immich_cfg.get("base_url", ""))


def get_api_key(config: dict | None = None) -> str:
    """取 Immich API key。"""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("api_key", "")


def get_default_album(config: dict | None = None) -> str | None:
    """取配置的默认相簿名。"""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("default_album") or None


def get_public_album_url(config: dict | None = None) -> str | None:
    """取默认相簿关联的公开分享地址。"""
    immich_cfg = get_immich_config(config)
    return immich_cfg.get("public_album_url", "").rstrip("/") or None


def get_asset_time_source(config: dict | None = None) -> str:
    """取上传资产的时间戳策略。"""
    immich_cfg = get_immich_config(config)
    source = str(immich_cfg.get("asset_time_source", "upload")).strip().lower()
    if source not in ASSET_TIME_SOURCES:
        choices = ", ".join(sorted(ASSET_TIME_SOURCES))
        raise ValueError(f"asset_time_source must be one of: {choices}")
    return source
