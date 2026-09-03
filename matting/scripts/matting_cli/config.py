"""Configuration parsing for the matting CLI."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_config import ConfigNotFoundError, load_section
from .errors import ConfigurationError

SKILL_DIR = Path(__file__).resolve().parents[2]
_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class MattingConfig:
    base_url: str
    timeout: float = 300.0
    poll_interval: float = 2.0
    max_wait_seconds: float = 900.0
    default_model: str | None = None
    max_input_bytes: int = 50 * 1024 * 1024
    max_pixels: int = 100_000_000
    headers: dict[str, str] = field(default_factory=dict)
    model_methods: dict[str, frozenset[str]] = field(default_factory=dict)
    source: Path | None = None


def _number(
    raw: dict[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    try:
        value = float(raw.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"[matting].{key} 必须是数字") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"[matting].{key} 必须在 {minimum}..{maximum} 之间")
    return value


def _integer(raw: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise ConfigurationError(f"[matting].{key} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"[matting].{key} 必须是整数") from exc
    if parsed < minimum:
        raise ConfigurationError(f"[matting].{key} 不能小于 {minimum}")
    return parsed


def _resolve_env(value: Any, label: str) -> str:
    text = str(value or "")
    match = _ENV_VALUE.fullmatch(text)
    if not match:
        return text
    resolved = os.environ.get(match.group(1), "")
    if not resolved:
        raise ConfigurationError(f"{label} 引用的环境变量未设置: {match.group(1)}")
    return resolved


def load_settings(
    path: str | Path | None = None, *, cwd: str | Path | None = None
) -> MattingConfig:
    selected = path or os.environ.get("MATTING_CONFIG")
    try:
        raw, source = load_section(
            "matting",
            SKILL_DIR,
            path=selected,
            cwd=cwd,
            missing="raise",
        )
    except (ConfigNotFoundError, OSError, TypeError) as exc:
        raise ConfigurationError(str(exc)) from exc
    if not raw:
        raise ConfigurationError(
            f"配置文件 {source} 缺少非空 [matting] section；示例: {SKILL_DIR / 'agent_config.example.toml'}"
        )
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigurationError("[matting].base_url 必须是 http:// 或 https:// URL")

    headers_raw = raw.get("headers", {})
    if not isinstance(headers_raw, dict):
        raise ConfigurationError("[matting.headers] 必须是 key/value 表")
    headers = {
        str(key): _resolve_env(value, f"[matting.headers].{key}")
        for key, value in headers_raw.items()
    }
    api_key_env = str(raw.get("api_key_env") or "").strip()
    api_key = str(raw.get("api_key") or "")
    if api_key_env:
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise ConfigurationError(
                f"[matting].api_key_env 指向的环境变量未设置: {api_key_env}"
            )
    else:
        api_key = _resolve_env(api_key, "[matting].api_key")
    if api_key:
        auth_header = str(raw.get("auth_header") or "Authorization")
        auth_scheme = str(raw.get("auth_scheme", "Bearer")).strip()
        headers[auth_header] = f"{auth_scheme} {api_key}".strip()

    model_methods: dict[str, frozenset[str]] = {}
    models = raw.get("models", {})
    if models and not isinstance(models, dict):
        raise ConfigurationError("[matting.models] 必须是模型子表")
    for model, value in models.items() if isinstance(models, dict) else ():
        if not isinstance(value, dict) or not isinstance(value.get("methods"), list):
            raise ConfigurationError(f"[matting.models.{model}] 必须声明 methods 数组")
        methods = frozenset(
            str(item).strip() for item in value["methods"] if str(item).strip()
        )
        if not methods:
            raise ConfigurationError(f"[matting.models.{model}].methods 不能为空")
        model_methods[str(model)] = methods

    return MattingConfig(
        base_url=base_url,
        timeout=_number(raw, "timeout", 300.0, 1.0, 3600.0),
        poll_interval=_number(raw, "poll_interval", 2.0, 0.1, 60.0),
        max_wait_seconds=_number(raw, "max_wait_seconds", 900.0, 1.0, 86_400.0),
        default_model=str(raw.get("default_model") or "").strip() or None,
        max_input_bytes=_integer(raw, "max_input_bytes", 50 * 1024 * 1024, 1),
        max_pixels=_integer(raw, "max_pixels", 100_000_000, 1),
        headers=headers,
        model_methods=model_methods,
        source=source,
    )
