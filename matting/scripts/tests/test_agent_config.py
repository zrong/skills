from pathlib import Path

import pytest

from matting_cli import agent_config
from matting_cli.config import load_settings
from matting_cli.errors import ConfigurationError


def test_lookup_priority_and_global_fallback(tmp_path: Path) -> None:
    cwd = tmp_path / "repo" / "work"
    skill_dir = tmp_path / "skill"
    git_config = tmp_path / "repo" / agent_config.CONFIG_FILENAME
    global_config = tmp_path / "home" / ".agents" / agent_config.CONFIG_FILENAME
    cwd.mkdir(parents=True)
    skill_dir.mkdir()
    (tmp_path / "repo" / ".git").mkdir()
    global_config.parent.mkdir(parents=True)
    locations = [
        global_config,
        git_config,
        skill_dir / agent_config.CONFIG_FILENAME,
        cwd / agent_config.CONFIG_FILENAME,
    ]
    for expected in locations:
        expected.write_text(
            "[matting]\nbase_url='http://example.test'\n", encoding="utf-8"
        )
        assert (
            agent_config.find_config(skill_dir, cwd=cwd, global_config=global_config)
            == expected.resolve()
        )


def test_explicit_missing_config_does_not_fallback(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path / "missing.toml", cwd=tmp_path)


def test_load_settings_parses_model_methods(tmp_path: Path) -> None:
    config = tmp_path / "agent_config.toml"
    config.write_text(
        "[matting]\nbase_url='http://example.test/'\ndefault_model='custom'\n"
        "[matting.models.custom]\nmethods=['birefnet']\n",
        encoding="utf-8",
    )
    settings = load_settings(config, cwd=tmp_path)
    assert settings.base_url == "http://example.test"
    assert settings.default_model == "custom"
    assert settings.model_methods["custom"] == frozenset({"birefnet"})
