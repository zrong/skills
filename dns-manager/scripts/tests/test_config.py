"""Config resolution: env indirection, validation, error messages."""

from pathlib import Path

import pytest

from dns_manager.config import ConfigError, load_skill_config


def _write_config(root: Path, body: str) -> Path:
    config_path = root / "agent_config.toml"
    config_path.write_text(body, encoding="utf-8")
    return config_path


def test_env_indirection_wins(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[dns-manager]",
                'provider = "tencentcloud"',
                "[dns-manager.tencentcloud]",
                'secret_id = "literal-id"',
                'secret_id_env = "DNSM_TEST_ID"',
                'secret_key_env = "DNSM_TEST_KEY"',
            ]
        ),
    )
    monkeypatch.setenv("DNSM_TEST_ID", "env-id")
    monkeypatch.setenv("DNSM_TEST_KEY", "env-key")
    config = load_skill_config(tmp_path / "skill", cwd=tmp_path)
    assert config.provider == "tencentcloud"
    assert config.provider_settings["secret_id"] == "env-id"
    assert config.provider_settings["secret_key"] == "env-key"
    assert config.resolved == {"secret_id": True, "secret_key": True}


def test_literal_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("DNSM_TEST_ID", raising=False)
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[dns-manager]",
                "[dns-manager.tencentcloud]",
                'secret_id = "id"  # provider defaults to tencentcloud',
                'secret_key = "key"',
            ]
        ),
    )
    config = load_skill_config(tmp_path / "skill", cwd=tmp_path)
    assert config.provider == "tencentcloud"
    assert config.provider_settings["secret_id"] == "id"


def test_missing_credentials_names_env_hint(monkeypatch, tmp_path):
    monkeypatch.delenv("DNSM_TEST_ID", raising=False)
    monkeypatch.delenv("DNSM_TEST_KEY", raising=False)
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[dns-manager]",
                "[dns-manager.tencentcloud]",
                'secret_id_env = "DNSM_TEST_ID"',
                'secret_key_env = "DNSM_TEST_KEY"',
            ]
        ),
    )
    with pytest.raises(ConfigError, match="DNSM_TEST_ID"):
        load_skill_config(tmp_path / "skill", cwd=tmp_path)


def test_unsupported_provider(tmp_path):
    _write_config(
        tmp_path,
        '[dns-manager]\nprovider = "notreal"\n[notreal]\nx = "1"\n',
    )
    with pytest.raises(ConfigError, match="Unsupported provider"):
        load_skill_config(tmp_path / "skill", cwd=tmp_path)


def test_missing_section_raises(tmp_path):
    _write_config(tmp_path, "[other-skill]\nenabled = true\n")
    with pytest.raises(ConfigError, match=r"\[dns-manager\]"):
        load_skill_config(tmp_path / "skill", cwd=tmp_path)
