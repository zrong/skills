from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from imggen.cli import main
from imggen.interactive import Markup, SessionStore, marked_prompt, normalize_markup
from imggen.models import ImageArtifact


def test_seedream_coordinate_markup() -> None:
    markup = normalize_markup([(100, 50)], [(0, 0, 200, 100)], (200, 100))
    assert markup == Markup(points=((500, 500),), boxes=((0, 0, 999, 999),))
    assert marked_prompt("替换人物", markup) == (
        "编辑位置：<point>500 500</point> <bbox>0 0 999 999</bbox>\n编辑指令：替换人物"
    )


def test_session_chains_latest_completed_output(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")
    output = tmp_path / "turn-1.png"
    output.write_bytes(b"output")
    store = SessionStore(tmp_path / "session.json")
    data = store.create("p", "seedream", "model", reference)
    index = store.begin_turn(data, "edit", "rendered", reference, Markup(), {"seed": 9})
    store.finish_turn(data, index, [output])
    recovered = store.load()
    assert store.latest_reference(recovered) == output
    assert recovered["turns"][0]["request_options"] == {"seed": 9}


def test_cli_rejects_unconfigured_model_without_network(
    config_file: Path, capsys
) -> None:
    code = main(
        [
            "generate",
            "draw",
            "--config",
            str(config_file),
            "-p",
            "test",
            "-e",
            "openai",
            "-m",
            "remote-only",
            "--dry-run",
        ]
    )
    assert code == 2
    assert "未被 endpoint" in capsys.readouterr().err


def test_batch_dry_run(config_file: Path, tmp_path: Path, capsys) -> None:
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text(
        json.dumps({"prompt": "one", "out": "named-one.png"})
        + "\n"
        + json.dumps({"prompt": "two", "out": "nested/named-two.png"})
        + "\n"
    )
    code = main(
        [
            "generate-batch",
            "--config",
            str(config_file),
            "-p",
            "test",
            "-e",
            "openai",
            "--input",
            str(jobs),
            "--out-dir",
            str(tmp_path / "out"),
            "--dry-run",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert output.count('"operation": "generate"') == 2
    assert str(tmp_path / "out" / "named-one.png") in output
    assert str(tmp_path / "out" / "named-two.png") in output


def test_batch_writes_outputs_and_refuses_duplicate_run(
    config_file: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text(
        json.dumps({"prompt": "one", "out": "one.png"})
        + "\n"
        + json.dumps({"prompt": "two"})
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "batch"
    calls = []

    def fake_execute(endpoint, request, max_attempts):
        calls.append(request.prompt)
        buffer = BytesIO()
        Image.new("RGB", (4, 4), "green").save(buffer, format="PNG")
        return [ImageArtifact(buffer.getvalue(), "image/png")]

    monkeypatch.setattr("imggen.cli.execute", fake_execute)
    command = [
        "generate-batch",
        "--config",
        str(config_file),
        "-p",
        "test",
        "-e",
        "openai",
        "--input",
        str(jobs),
        "--out-dir",
        str(out_dir),
        "--concurrency",
        "2",
        "--no-augment",
    ]
    assert main(command) == 0
    assert sorted(path.name for path in out_dir.glob("*.png")) == [
        "job_2.png",
        "one.png",
    ]
    first_call_count = len(calls)
    assert main(command) == 2
    assert len(calls) == first_call_count
    assert "输出已存在" in capsys.readouterr().err


def test_interactive_cli_session_and_retry(
    config_file: Path, tmp_path: Path, monkeypatch
) -> None:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(reference)
    session = tmp_path / "session.json"
    first_output = tmp_path / "turn-1.png"
    calls = []

    def fake_execute(endpoint, request, max_attempts):
        calls.append(request)
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
        return [ImageArtifact(buffer.getvalue(), "image/png")]

    monkeypatch.setattr("imggen.cli.execute", fake_execute)
    code = main(
        [
            "interactive",
            "start",
            "--session",
            str(session),
            "--image",
            str(reference),
            "--prompt",
            "replace",
            "--point",
            "500,500",
            "--no-augment",
            "--config",
            str(config_file),
            "-p",
            "test",
            "-e",
            "seedream",
            "-o",
            str(first_output),
        ]
    )
    assert code == 0
    assert "<point>500 500</point>" in calls[0].prompt
    assert (
        SessionStore(session).latest_reference(SessionStore(session).load())
        == first_output
    )

    store = SessionStore(session)
    data = store.load()
    failed_index = store.begin_turn(
        data, "retry", "rendered retry", first_output, Markup(), {"seed": 9}
    )
    store.fail_turn(data, failed_index, RuntimeError("transient"))
    retry_output = tmp_path / "retry.png"
    code = main(
        [
            "interactive",
            "retry",
            "--session",
            str(session),
            "--config",
            str(config_file),
            "--out",
            str(retry_output),
        ]
    )
    assert code == 0
    assert calls[-1].seed == 9
    assert calls[-1].prompt == "rendered retry"
    assert store.load()["turns"][-1]["status"] == "completed"
