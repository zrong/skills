from __future__ import annotations

from pathlib import Path

import pytest

from filebrowser_transfer.agent_config import ConfigNotFoundError
from filebrowser_transfer.config import ConfigError, load_skill_config

CONFIG = """
[filebrowser]
default_source = "main"
default_target = "archive"

[filebrowser.sources.main]
adapter = "filebrowser"
base_url = "https://files.example.test"
token_env = "TEST_FILEBROWSER_TOKEN"
source = "projects"
max_transfer_bytes = 1000

[filebrowser.targets.archive]
adapter = "s3"
bucket = "archive-bucket"
region = "ap-guangzhou"
endpoint_url = "https://cos.example.test"
public_base_url = "https://cdn.example.test"
prefix = "backups"
access_key_id_env = "TEST_S3_ACCESS_KEY"
secret_access_key_env = "TEST_S3_SECRET_KEY"
addressing_style = "virtual"

[filebrowser.targets.secondary]
adapter = "s3"
bucket = "secondary-bucket"
profile = "secondary"
prefix = "mirror"
"""


def test_loads_multiple_s3_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("TEST_FILEBROWSER_TOKEN", "fb-secret")
    config, found = load_skill_config(config_path)

    assert found == config_path.resolve()
    assert config.default_source == "main"
    assert config.default_target == "archive"
    assert list(config.targets) == ["archive", "secondary"]
    assert config.source().token.resolve("token") == "fb-secret"
    assert config.source().upload_chunk_bytes == 16 * 1024 * 1024
    assert config.target("secondary").bucket == "secondary-bucket"
    assert "fb-secret" not in repr(config)


def test_global_config_is_final_fallback(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    cwd.mkdir()
    global_config = tmp_path / "home" / ".agents" / "agent_config.toml"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(CONFIG, encoding="utf-8")
    config, found = load_skill_config(cwd=cwd, global_config=global_config)
    assert found == global_config.resolve()
    assert config.default_target == "archive"


def test_explicit_missing_config_does_not_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback.toml"
    fallback.write_text(CONFIG, encoding="utf-8")
    with pytest.raises(ConfigNotFoundError):
        load_skill_config(
            tmp_path / "missing.toml",
            cwd=tmp_path,
            global_config=fallback,
        )


def test_rejects_unknown_target_adapter(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(CONFIG.replace('adapter = "s3"', 'adapter = "ftp"', 1), encoding="utf-8")
    with pytest.raises(ConfigError, match="Unsupported target adapter"):
        load_skill_config(config_path)


def test_rejects_invalid_default_target(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        CONFIG.replace('default_target = "archive"', 'default_target = "missing"'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="default_target does not exist"):
        load_skill_config(config_path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('secret_access_key_env = ""', "must declare both"),
        ('profile = "archive"\n', "cannot combine profile"),
        (
            'access_key_id_env = ""\nsecret_access_key_env = ""\n'
            'session_token_env = "SESSION_TOKEN"',
            "cannot declare session_token",
        ),
    ],
)
def test_rejects_ambiguous_s3_credentials(
    tmp_path: Path,
    replacement: str,
    message: str,
) -> None:
    config_text = CONFIG
    if replacement.startswith('profile = "archive"'):
        config_text = config_text.replace(
            'bucket = "archive-bucket"', 'bucket = "archive-bucket"\n' + replacement
        )
    elif replacement.startswith('secret_access_key_env = ""'):
        config_text = config_text.replace(
            'secret_access_key_env = "TEST_S3_SECRET_KEY"',
            replacement,
        )
    else:
        config_text = config_text.replace(
            'access_key_id_env = "TEST_S3_ACCESS_KEY"\n'
            'secret_access_key_env = "TEST_S3_SECRET_KEY"',
            replacement,
        )
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(config_text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_skill_config(config_path)


def test_supports_source_only_config_for_filebrowser_put(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[filebrowser]

[filebrowser.sources.main]
base_url = "https://files.example.test"
token_env = "TEST_FILEBROWSER_TOKEN"
source = "projects"
upload_chunk_bytes = 4194304
""",
        encoding="utf-8",
    )
    config, _ = load_skill_config(config_path)

    assert config.default_source == "main"
    assert config.default_target == ""
    assert config.source().upload_chunk_bytes == 4 * 1024 * 1024
