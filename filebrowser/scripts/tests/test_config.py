from __future__ import annotations

from pathlib import Path

import pytest

from filebrowser_transfer.config import ConfigError, load_skill_config

CONFIG = """
[filebrowser]
default_source = "primary"
staging_dir = "~/staging"

[filebrowser.sources.primary]
adapter = "filebrowser"
base_url = "https://files.example.test/"
token_env = "FILEBROWSER_TOKEN"
source = "projects"
timeout_seconds = 30
max_transfer_bytes = 1024
upload_chunk_bytes = 128

[filebrowser.sources.secondary]
base_url = "https://backup.example.test"
token = "secret"
source = "backup"
verify_tls = false
"""


def test_loads_multiple_filebrowser_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(CONFIG, encoding="utf-8")
    config, discovered = load_skill_config(config_path)

    assert discovered == config_path
    assert config.default_source == "primary"
    assert list(config.sources) == ["primary", "secondary"]
    assert config.source().base_url == "https://files.example.test"
    assert config.source().upload_chunk_bytes == 128
    assert config.source("secondary").verify_tls is False
    assert config.staging_dir == Path("~/staging").expanduser().resolve()


def test_defaults_to_first_source(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]
[filebrowser.sources.main]
base_url = "https://files.example.test"
source = "default"
token_env = "FILEBROWSER_TOKEN"
""".strip(),
        encoding="utf-8",
    )
    config, _ = load_skill_config(config_path)
    assert config.default_source == "main"


def test_rejects_unknown_default_source(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(CONFIG.replace('default_source = "primary"', 'default_source = "x"'))
    with pytest.raises(ConfigError, match="default_source does not exist"):
        load_skill_config(config_path)


def test_rejects_non_http_base_url(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(CONFIG.replace("https://files.example.test/", "file:///tmp"))
    with pytest.raises(ConfigError, match=r"HTTP\(S\) URL"):
        load_skill_config(config_path)


def test_rejects_missing_sources(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text("[filebrowser]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"filebrowser\.sources"):
        load_skill_config(config_path)
