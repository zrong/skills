from __future__ import annotations

from pathlib import Path

import pytest

from object_storage.config import ConfigError, load_skill_config, parse_skill_config


def _document() -> dict[str, object]:
    return {
        "object-storage": {
            "default_target": "archive",
            "targets": {
                "archive": {
                    "adapter": "s3",
                    "bucket": "bucket",
                    "endpoint_url": "https://cos.example.test",
                    "prefix": "backup/files",
                    "access_key_id_env": "S3_ACCESS_KEY",
                    "secret_access_key_env": "S3_SECRET_KEY",
                    "cdn": {
                        "provider": "tencent",
                        "base_url": "https://cdn.example.test",
                    },
                }
            },
        }
    }


def test_parse_independent_object_storage_section() -> None:
    config = parse_skill_config(_document())  # type: ignore[arg-type]
    target = config.target()

    assert config.default_target == "archive"
    assert target.bucket == "bucket"
    assert target.prefix == "backup/files"
    assert target.cdn is not None
    assert target.cdn.base_url == "https://cdn.example.test"
    assert target.cdn.access_key_id.env_var == "S3_ACCESS_KEY"


def test_rejects_missing_targets() -> None:
    with pytest.raises(ConfigError, match=r"object-storage\.targets"):
        parse_skill_config({"object-storage": {}})


def test_rejects_unknown_default_target() -> None:
    document = _document()
    section = document["object-storage"]
    assert isinstance(section, dict)
    section["default_target"] = "missing"
    with pytest.raises(ConfigError, match="default_target does not exist"):
        parse_skill_config(document)  # type: ignore[arg-type]


def test_load_uses_explicit_config(tmp_path: Path) -> None:
    config_path = tmp_path / "agent_config.toml"
    config_path.write_text(
        """
[object-storage]
default_target = "archive"

[object-storage.targets.archive]
adapter = "s3"
bucket = "bucket"
""".strip(),
        encoding="utf-8",
    )
    config, discovered = load_skill_config(config_path)
    assert discovered == config_path
    assert config.target().bucket == "bucket"
