import json
import subprocess
from pathlib import Path

from upscale_cli.config import UpscaleConfig
from upscale_cli.filebrowser import FileBrowserGateway


def test_gateway_delegates_to_filebrowser_cli_without_forcing_upscale_config(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "filebrowser" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "pyproject.toml").write_text("[project]\nname='fb'\nversion='0.1'\n")
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "source_name": "production",
                    "remote_path": "/a.png",
                    "local_path": "/tmp/a.png",
                    "size": 10,
                }
            ),
            stderr="",
        )

    gateway = FileBrowserGateway(
        UpscaleConfig(base_url="http://api.test"),
        scripts_dir=scripts,
        runner=runner,
    )
    gateway.get("/a.png", Path("/tmp/a.png"), source="production", dry_run=True)
    command = commands[0]
    assert command[:5] == [
        "uv",
        "run",
        "--project",
        str(scripts),
        "filebrowser",
    ]
    assert "--config" not in command
    assert command[-2:] == ["--dry-run", "--json"]


def test_gateway_uses_explicit_filebrowser_config(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "pyproject.toml").write_text("[project]\nname='fb'\nversion='0.1'\n")
    config_path = tmp_path / "filebrowser.toml"
    config_path.write_text("[filebrowser]\n")
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"source_name":"x","remote_path":"/a","size":1}',
            stderr="",
        )

    gateway = FileBrowserGateway(
        UpscaleConfig(base_url="http://api.test", filebrowser_config=config_path),
        scripts_dir=scripts,
        runner=runner,
    )
    local = tmp_path / "result.png"
    local.write_bytes(b"x")
    gateway.put(local, "/result.png")
    index = commands[0].index("--config")
    assert commands[0][index + 1] == str(config_path)
