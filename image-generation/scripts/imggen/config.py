"""Strict agent_config.toml loading and endpoint/model allowlist resolution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import tomllib

from imggen.models import ConfigError, EndpointConfig, ModelPolicy


SKILL_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_EXAMPLE_PATH = SKILL_DIR / "agent_config.example.toml"
_ENV_VALUE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def find_config(explicit: str | Path | None = None) -> Path:
    """Resolve config without silently crossing into unrelated files."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    elif os.environ.get("IMAGEGEN_CONFIG"):
        candidates.append(Path(os.environ["IMAGEGEN_CONFIG"]).expanduser())
    else:
        candidates.extend(
            [Path.cwd() / "agent_config.toml", SKILL_DIR / "agent_config.toml"]
        )
        for parent in (Path.cwd(), *Path.cwd().parents):
            if (parent / ".git").exists():
                candidates.append(parent / "agent_config.toml")
                break

    for candidate in dict.fromkeys(path.resolve() for path in candidates):
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates) or "(无候选路径)"
    raise FileNotFoundError(
        f"未找到 agent_config.toml；搜索了: {searched}\n"
        f"可设置 --config/IMAGEGEN_CONFIG，示例: {CONFIG_EXAMPLE_PATH}"
    )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = find_config(path)
    try:
        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件 TOML 无效: {config_path}: {exc}") from exc


def _image_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("image-generation")
    if not isinstance(value, dict):
        raise ConfigError("缺少 [image-generation] 配置")
    return value


def _secret(value: Any, env_name: Any, label: str, inherited: Any = None) -> str:
    if env_name:
        resolved = os.environ.get(str(env_name), "")
        if not resolved:
            raise ConfigError(f"{label}.api_key_env 指向的环境变量未设置: {env_name}")
        return resolved
    if value == "@provider":
        if not isinstance(inherited, str) or not inherited:
            raise ConfigError(
                f"{label}.api_key=@provider，但 provider 层没有可引用的 api_key"
            )
        value = inherited
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} 必须独立配置 api_key 或 api_key_env")
    match = _ENV_VALUE.fullmatch(value)
    if match:
        resolved = os.environ.get(match.group(1), "")
        if not resolved:
            raise ConfigError(f"{label}.api_key 引用的环境变量未设置: {match.group(1)}")
        return resolved
    return value


def _parse_endpoint(
    provider_key: str,
    provider: dict[str, Any],
    endpoint_key: str,
    raw: dict[str, Any],
) -> EndpointConfig:
    label = f"image-generation.providers.{provider_key}.endpoints.{endpoint_key}"
    adapter = str(raw.get("adapter", "")).lower()
    if adapter not in {"openai", "gemini", "seedream"}:
        raise ConfigError(f"{label}.adapter 必须是 openai/gemini/seedream")
    auth = str(raw.get("auth", "x-goog-api-key" if adapter == "gemini" else "bearer"))
    if auth not in {"bearer", "x-goog-api-key"}:
        raise ConfigError(f"{label}.auth 必须是 bearer 或 x-goog-api-key")
    base_url = str(raw.get("base_url", "")).rstrip("/")
    if not base_url:
        raise ConfigError(f"{label}.base_url 不能为空")
    raw_models = raw.get("models")
    if not isinstance(raw_models, dict) or not raw_models:
        raise ConfigError(
            f"{label}.models 必须是非空精确 allowlist；远端返回的其他模型不会自动获准调用"
        )
    models = {
        str(name): ModelPolicy.from_dict(str(name), data, adapter)
        for name, data in raw_models.items()
        if isinstance(data, dict)
    }
    if len(models) != len(raw_models):
        raise ConfigError(f"{label}.models 的每个模型都必须使用 TOML 子表声明")
    default_model = str(raw.get("default_model", ""))
    if default_model and default_model not in models:
        raise ConfigError(
            f"{label}.default_model '{default_model}' 不在该 endpoint 的 models allowlist"
        )
    headers = raw.get("headers", {})
    if not isinstance(headers, dict):
        raise ConfigError(f"{label}.headers 必须是 key/value 表")
    return EndpointConfig(
        provider_key=provider_key,
        provider_name=str(provider.get("name", provider_key)),
        endpoint_key=endpoint_key,
        adapter=adapter,
        base_url=base_url,
        api_key=_secret(
            raw.get("api_key"), raw.get("api_key_env"), label, provider.get("api_key")
        ),
        auth=auth,
        default_model=default_model,
        models=models,
        timeout=float(raw.get("timeout", 180)),
        headers={str(k): str(v) for k, v in headers.items()},
    )


def get_endpoint_config(
    provider: str | None = None,
    endpoint: str | None = None,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> EndpointConfig:
    image = _image_config(config or load_config(config_path))
    providers = image.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ConfigError("未配置 [image-generation.providers.*]")
    provider_key = provider or str(image.get("default_provider", ""))
    if not provider_key:
        provider_key = next(iter(providers))
    raw_provider = providers.get(provider_key)
    if not isinstance(raw_provider, dict):
        raise ConfigError(
            f"Provider '{provider_key}' 不存在；可用: {', '.join(providers)}"
        )
    endpoints = raw_provider.get("endpoints")
    if not isinstance(endpoints, dict) or not endpoints:
        raise ConfigError(
            f"Provider '{provider_key}' 仍是旧格式；请迁移到 endpoints.*，并为每个 endpoint "
            "独立声明 adapter/base_url/api_key/models"
        )
    endpoint_key = endpoint or str(raw_provider.get("default_endpoint", ""))
    if not endpoint_key:
        endpoint_key = next(iter(endpoints))
    raw_endpoint = endpoints.get(endpoint_key)
    if not isinstance(raw_endpoint, dict):
        raise ConfigError(
            f"Endpoint '{endpoint_key}' 不存在于 provider '{provider_key}'；可用: "
            f"{', '.join(endpoints)}"
        )
    return _parse_endpoint(provider_key, raw_provider, endpoint_key, raw_endpoint)


def list_providers(
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    image = _image_config(config or load_config(config_path))
    providers = image.get("providers", {})
    default = str(image.get("default_provider", ""))
    result: list[dict[str, Any]] = []
    for provider_key, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        endpoint_rows: list[dict[str, Any]] = []
        for endpoint_key, raw in provider.get("endpoints", {}).items():
            if not isinstance(raw, dict):
                continue
            endpoint_rows.append(
                {
                    "key": endpoint_key,
                    "adapter": raw.get("adapter", "(missing)"),
                    "base_url": raw.get("base_url", ""),
                    "default_model": raw.get("default_model", ""),
                    "models": sorted((raw.get("models") or {}).keys()),
                }
            )
        result.append(
            {
                "key": provider_key,
                "name": provider.get("name", provider_key),
                "is_default": provider_key == default,
                "endpoints": endpoint_rows,
            }
        )
    return result
