import json
from pathlib import Path
from subprocess import CompletedProcess

from PIL import Image

from imggen import matting_bridge


def test_configured_matting_is_used_without_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "result.png"
    Image.new("RGB", (4, 4), "green").save(source)
    project = tmp_path / "matting" / "scripts"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='fake'\nversion='1'\n", encoding="utf-8"
    )
    monkeypatch.setattr(matting_bridge, "_matting_project", lambda: project)

    def fake_run(command, *, timeout=None):
        if "status" in command:
            return CompletedProcess(command, 0, json.dumps({"available": True}), "")
        output.write_bytes(b"fake")
        return CompletedProcess(
            command,
            0,
            json.dumps({"backend": "matting-api", "output": str(output)}),
            "",
        )

    monkeypatch.setattr(matting_bridge, "_run", fake_run)
    result = matting_bridge.remove_background(str(source), str(output))
    assert result["backend"] == "matting-api"
    assert result["integration"] == "image-generation"


def test_missing_matting_config_falls_back_to_chroma(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "result.png"
    Image.new("RGB", (8, 8), "#00ff00").save(source)
    project = tmp_path / "matting" / "scripts"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='fake'\nversion='1'\n", encoding="utf-8"
    )
    monkeypatch.setattr(matting_bridge, "_matting_project", lambda: project)
    monkeypatch.setattr(
        matting_bridge,
        "_run",
        lambda command, timeout=None: CompletedProcess(
            command, 2, "", "配置错误: 缺少 [matting]"
        ),
    )
    result = matting_bridge.remove_background(str(source), str(output))
    assert result["backend"] == "chroma-key"
    assert "缺少 [matting]" in result["fallback_reason"]
    assert output.is_file()


def test_matting_execution_failure_does_not_silently_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "result.png"
    Image.new("RGB", (4, 4), "white").save(source)
    project = tmp_path / "matting" / "scripts"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='fake'\nversion='1'\n", encoding="utf-8"
    )
    monkeypatch.setattr(matting_bridge, "_matting_project", lambda: project)
    calls = 0

    def fake_run(command, *, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CompletedProcess(command, 0, json.dumps({"available": True}), "")
        return CompletedProcess(command, 1, "", "task failed")

    monkeypatch.setattr(matting_bridge, "_run", fake_run)
    try:
        matting_bridge.remove_background(str(source), str(output))
    except Exception as exc:
        assert "拒绝静默回退" in str(exc)
    else:
        raise AssertionError("expected matting failure")
    assert not output.exists()
