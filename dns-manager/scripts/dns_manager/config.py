"""Load [dns-manager] settings from agent_config.toml with env-var secrets.

Credentials resolve in this order per field:
1. Environment variable named by ``<field>_env`` (e.g. ``secret_id_env``).
2. The literal ``<field>`` value in the TOML section.
Values are never printed; ``doctor`` only reports whether they resolved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .agent_config import ConfigNotFoundError, load_section
from .providers import PROVIDER_NAMES

SECTION = "dns-manager"
DEFAULT_PROVIDER = "tencentcloud"


class ConfigError(Exception):
    """Raised for invalid or incomplete dns-manager configuration."""


@dataclass(frozen=True)
class SkillConfig:
    provider: str
    provider_settings: dict
    config_path: Path | None
    resolved: dict[str, bool]


def _resolve_secret(section: dict, field: str) -> tuple[str | None, bool]:
    """Resolve a secret from env indirection first, then the TOML value."""
    env_name = section.get(f"{field}_env")
    if isinstance(env_name, str) and env_name:
        value = os.environ.get(env_name)
        if value:
            return value, True
    value = section.get(field)
    if isinstance(value, str) and value:
        return value, True
    return None, False


def load_skill_config(
    skill_dir: Path,
    path: str | None = None,
    cwd: Path | None = None,
) -> SkillConfig:
    """Load and validate the [dns-manager] section."""
    try:
        section, config_path = load_section(
            SECTION,
            skill_dir,
            path=path,
            cwd=cwd,
            missing="raise",
        )
    except ConfigNotFoundError as exc:
        raise ConfigError(str(exc)) from exc

    if not section:
        raise ConfigError(
            f"Section [{SECTION}] not found in {config_path}. "
            "See agent_config.example.toml for the expected fields."
        )

    provider = section.get("provider", DEFAULT_PROVIDER)
    if not isinstance(provider, str) or provider not in PROVIDER_NAMES:
        supported = ", ".join(sorted(PROVIDER_NAMES))
        raise ConfigError(
            f"Unsupported provider {provider!r}; supported: {supported}. "
            f"Set [{SECTION}] provider in {config_path}."
        )

    settings = section.get(provider, {})
    if not isinstance(settings, dict):
        raise ConfigError(f"[{SECTION}.{provider}] must be a TOML table")

    resolved = {
        field: _resolve_secret(settings, field)[1]
        for field in ("secret_id", "secret_key")
    }
    missing = [f for f, ok in resolved.items() if not ok]
    if missing:
        env_hint = ", ".join(
            f"{f}_env -> {settings.get(f + '_env', '<unset>')}" for f in missing
        )
        raise ConfigError(
            f"Provider {provider!r} is missing credentials: {', '.join(missing)} "
            f"(configure them in [{SECTION}.{provider}] or via env vars: {env_hint})"
        )

    provider_settings = dict(settings)
    for field in ("secret_id", "secret_key"):
        provider_settings[field] = _resolve_secret(settings, field)[0]

    return SkillConfig(
        provider=provider,
        provider_settings=provider_settings,
        config_path=config_path,
        resolved=resolved,
    )
