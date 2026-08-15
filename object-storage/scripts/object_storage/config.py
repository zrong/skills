"""解析独立的 [object-storage] 严格配置。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from urllib.parse import urlparse

from .agent_config import TomlTable, TomlValue, load_config
from .models import (
    CdnConfig,
    ConfigurationError,
    ObjectStorageConfig,
    S3TargetConfig,
    SecretValue,
)

SKILL_DIR = Path(__file__).resolve().parents[2]
CONFIG_SECTION = "object-storage"
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


def _int(table: Mapping[str, TomlValue], key: str, *, default: int, minimum: int) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{key} must be an integer >= {minimum}")
    return value


def _secret(table: Mapping[str, TomlValue], key: str) -> SecretValue:
    return SecretValue(direct=_string(table, key), env_var=_string(table, f"{key}_env"))


def _http_url(value: str, label: str, *, required: bool) -> str:
    if not value and not required:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{label} must be an HTTP(S) URL")
    return value.rstrip("/")


def _relative_prefix(value: str, label: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(f"{label} must be a relative S3 key prefix without '..'")
    return "/".join(part for part in path.parts if part not in {"", "."})


def _parse_cdn(
    name: str,
    table: Mapping[str, TomlValue],
    target_access_key_id: SecretValue,
    target_secret_access_key: SecretValue,
) -> CdnConfig | None:
    if "cdn" not in table:
        return None
    cdn_table = _table(table["cdn"], f"[object-storage.targets.{name}.cdn]")
    provider = _string(cdn_table, "provider")
    if provider != "tencent":
        raise ConfigError(f"Unsupported CDN provider for target {name}: {provider or '(missing)'}")
    access_key_id = _secret(cdn_table, "access_key_id")
    secret_access_key = _secret(cdn_table, "secret_access_key")
    if access_key_id.declared != secret_access_key.declared:
        raise ConfigError(f"CDN target {name} must declare both credentials")
    if not access_key_id.declared:
        if not target_access_key_id.declared:
            raise ConfigError(
                f"CDN target {name} must declare credentials when S3 uses a profile/default chain"
            )
        access_key_id = target_access_key_id
        secret_access_key = target_secret_access_key
    return CdnConfig(
        name=name,
        provider=provider,
        base_url=_http_url(
            _required_string(cdn_table, "base_url", f"object-storage.targets.{name}.cdn"),
            f"object-storage.targets.{name}.cdn.base_url",
            required=True,
        ),
        purge_on_upload=_bool(cdn_table, "purge_on_upload", default=False),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def _parse_target(name: str, value: TomlValue) -> S3TargetConfig:
    label = f"object-storage.targets.{name}"
    table = _table(value, f"[{label}]")
    adapter = _string(table, "adapter", default="s3")
    if adapter != "s3":
        raise ConfigError(f"Unsupported target adapter for {name}: {adapter}")
    addressing_style = _string(table, "addressing_style", default="auto")
    if addressing_style not in {"auto", "path", "virtual"}:
        raise ConfigError(f"Invalid addressing_style for target {name}: {addressing_style}")
    profile = _string(table, "profile")
    access_key_id = _secret(table, "access_key_id")
    secret_access_key = _secret(table, "secret_access_key")
    session_token = _secret(table, "session_token")
    if access_key_id.declared != secret_access_key.declared:
        raise ConfigError(f"S3 target {name} must declare both access_key_id and secret_access_key")
    if profile and access_key_id.declared:
        raise ConfigError(f"S3 target {name} cannot combine profile with explicit credentials")
    if session_token.declared and not access_key_id.declared:
        raise ConfigError(f"S3 target {name} cannot declare session_token without credentials")
    return S3TargetConfig(
        name=name,
        bucket=_required_string(table, "bucket", label),
        region=_string(table, "region"),
        endpoint_url=_http_url(
            _string(table, "endpoint_url"), f"{label}.endpoint_url", required=False
        ),
        public_base_url=_http_url(
            _string(table, "public_base_url"), f"{label}.public_base_url", required=False
        ),
        prefix=_relative_prefix(_string(table, "prefix"), f"{label}.prefix"),
        profile=profile,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        addressing_style=cast(Literal["auto", "path", "virtual"], addressing_style),
        storage_class=_string(table, "storage_class"),
        multipart_threshold_bytes=_int(
            table, "multipart_threshold_bytes", default=8 * 1024 * 1024, minimum=1
        ),
        multipart_chunksize_bytes=_int(
            table,
            "multipart_chunksize_bytes",
            default=8 * 1024 * 1024,
            minimum=5 * 1024 * 1024,
        ),
        max_concurrency=_int(table, "max_concurrency", default=4, minimum=1),
        verify_tls=_bool(table, "verify_tls", default=True),
        cdn=_parse_cdn(name, table, access_key_id, secret_access_key),
    )


def parse_skill_config(document: TomlTable) -> ObjectStorageConfig:
    root = _table(document.get(CONFIG_SECTION), f"[{CONFIG_SECTION}]")
    target_values = _table(root.get("targets"), "[object-storage.targets]")
    targets = {name: _parse_target(name, value) for name, value in target_values.items()}
    if not targets:
        raise ConfigError("At least one [object-storage.targets.<name>] is required")
    default_target = _string(root, "default_target") or next(iter(targets))
    if default_target not in targets:
        raise ConfigError(f"default_target does not exist: {default_target}")
    return ObjectStorageConfig(targets=targets, default_target=default_target)


def load_skill_config(
    path: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    global_config: str | Path | None = None,
) -> tuple[ObjectStorageConfig, Path]:
    document, config_path = load_config(SKILL_DIR, path=path, cwd=cwd, global_config=global_config)
    return parse_skill_config(document), config_path
