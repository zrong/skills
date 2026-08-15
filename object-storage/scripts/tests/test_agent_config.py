from __future__ import annotations

from pathlib import Path

import pytest

from object_storage.agent_config import ConfigNotFoundError, config_candidates, find_config


def test_candidates_follow_cwd_skill_git_global_order(tmp_path: Path) -> None:
    project = tmp_path / "project"
    cwd = project / "work"
    skill = tmp_path / "skill"
    global_config = tmp_path / "home" / "agent_config.toml"
    cwd.mkdir(parents=True)
    skill.mkdir()
    (project / ".git").mkdir()

    candidates = config_candidates(skill, cwd=cwd, global_config=global_config)

    assert candidates == (
        (cwd / "agent_config.toml").resolve(),
        (skill / "agent_config.toml").resolve(),
        (project / "agent_config.toml").resolve(),
        global_config.resolve(),
    )


def test_find_config_prefers_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    skill = tmp_path / "skill"
    cwd.mkdir()
    skill.mkdir()
    cwd_config = cwd / "agent_config.toml"
    skill_config = skill / "agent_config.toml"
    cwd_config.write_text("[object-storage]\n", encoding="utf-8")
    skill_config.write_text("[object-storage]\n", encoding="utf-8")

    assert find_config(skill, cwd=cwd, global_config=tmp_path / "global.toml") == (
        cwd_config.resolve()
    )


def test_explicit_missing_path_does_not_fall_back(tmp_path: Path) -> None:
    global_config = tmp_path / "global.toml"
    global_config.write_text("[object-storage]\n", encoding="utf-8")
    missing = tmp_path / "missing.toml"

    with pytest.raises(ConfigNotFoundError) as captured:
        find_config(tmp_path / "skill", path=missing, global_config=global_config)

    assert captured.value.searched_paths == (missing.resolve(),)


def test_global_config_is_last_fallback(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    skill = tmp_path / "skill"
    global_config = tmp_path / "global.toml"
    cwd.mkdir()
    skill.mkdir()
    global_config.write_text("[object-storage]\n", encoding="utf-8")

    assert find_config(skill, cwd=cwd, global_config=global_config) == global_config.resolve()
