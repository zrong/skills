"""解析独立的 FileBrowser 多源配置。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from .agent_config import TomlTable, TomlValue, load_config
from .models import ConfigurationError, FileBrowserSourceConfig, SecretValue, SkillConfig

SKILL_DIR = Path(__file__).resolve().parents[2]
CONFIG_SECTION = "filebrowser"
ConfigError = ConfigurationError


def _table(value: TomlValue | None, label: str) -> dict[str, TomlValue]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a TOML table")
    return value


def _string(table: Mapping[str, TomlValue], key: str, *, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value.strip()


def _required_string(table: Mapping[str, TomlValue], key: str, label: str) -> str:
    value = _string(table, key)
    if not value:
        raise ConfigError(f"{label}.{key} must be a non-empty string")
    return value


def _bool(table: Mapping[str, TomlValue], key: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _int(table: Mapping[str, TomlValue], key: str, *, default: int, minimum: int = 0) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{key} must be an integer >= {minimum}")
    return value


def _float(table: Mapping[str, TomlValue], key: str, *, default: float, minimum: float) -> float:
    value = table.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{key} must be a number >= {minimum}")
    return float(value)


def _secret(table: Mapping[str, TomlValue], key: str) -> SecretValue:
    return SecretValue(direct=_string(table, key), env_var=_string(table, f"{key}_env"))


def _http_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{label} must be an HTTP(S) URL")
    return value.rstrip("/")


def _parse_source(name: str, value: TomlValue) -> FileBrowserSourceConfig:
    label = f"filebrowser.sources.{name}"
    table = _table(value, f"[{label}]")
    adapter = _string(table, "adapter", default="filebrowser")
    if adapter != "filebrowser":
        raise ConfigError(f"Unsupported source adapter for {name}: {adapter}")
    return FileBrowserSourceConfig(
        name=name,
        base_url=_http_url(_required_string(table, "base_url", label), f"{label}.base_url"),
        token=_secret(table, "token"),
        source=_required_string(table, "source", label),
        verify_tls=_bool(table, "verify_tls", default=True),
        timeout_seconds=_float(table, "timeout_seconds", default=600.0, minimum=1.0),
        max_transfer_bytes=_int(table, "max_transfer_bytes", default=0),
        upload_chunk_bytes=_int(table, "upload_chunk_bytes", default=16 * 1024 * 1024, minimum=1),
    )


def parse_skill_config(document: TomlTable) -> SkillConfig:
    root = _table(document.get(CONFIG_SECTION), f"[{CONFIG_SECTION}]")
    source_values = _table(root.get("sources"), "[filebrowser.sources]")
    sources = {name: _parse_source(name, value) for name, value in source_values.items()}
    if not sources:
        raise ConfigError("At least one [filebrowser.sources.<name>] is required")
    default_source = _string(root, "default_source") or next(iter(sources))
    if default_source not in sources:
        raise ConfigError(f"default_source does not exist: {default_source}")
    staging_value = _string(root, "staging_dir")
    staging_dir = Path(staging_value).expanduser().resolve() if staging_value else None
    return SkillConfig(
        sources=sources,
        default_source=default_source,
        staging_dir=staging_dir,
    )


def load_skill_config(
    path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
) -> tuple[SkillConfig, Path]:
    document, config_path = load_config(
        SKILL_DIR,
        path=path,
        cwd=cwd,
        global_config=global_config,
    )
    return parse_skill_config(document), config_path
