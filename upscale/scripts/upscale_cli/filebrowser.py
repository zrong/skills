from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from .agent_config import find_git_root
from .config import SKILL_DIR, UpscaleConfig
from .errors import FileBrowserError
from .naming import default_output_name

Runner = Callable[..., subprocess.CompletedProcess[str]]


def normalize_remote_path(value: str) -> str:
    text = value.strip().replace("\\", "/")
    pure = PurePosixPath(text)
    if not text.startswith("/") or ".." in pure.parts:
        raise FileBrowserError(f"FileBrowser 路径必须是无 .. 的绝对路径: {value}")
    return str(pure)


def remote_output_path(
    remote_input: str, media_type: str, model: str, *, width: int | None,
    height: int | None, fps=None
) -> str:
    source = PurePosixPath(normalize_remote_path(remote_input))
    name = default_output_name(
        source.name, media_type, model, width=width, height=height, fps=fps
    )
    return str(source.with_name(name))


def discover_filebrowser_scripts(config: UpscaleConfig) -> Path:
    env_value = os.environ.get("FILEBROWSER_SKILL_DIR", "").strip()
    candidates: list[Path] = []
    if config.filebrowser_skill_dir:
        candidates.append(config.filebrowser_skill_dir)
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.append(SKILL_DIR.parent / "filebrowser")
    git_root = find_git_root(Path.cwd())
    if git_root:
        candidates.append(git_root / ".agents" / "skills" / "filebrowser")
    candidates.append(Path.home() / ".agents" / "skills" / "filebrowser")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        scripts = (
            resolved
            if (resolved / "pyproject.toml").is_file()
            else resolved / "scripts"
        )
        scripts = scripts.resolve()
        if scripts in seen:
            continue
        seen.add(scripts)
        if (scripts / "pyproject.toml").is_file():
            return scripts
    searched = "\n  ".join(str(item) for item in candidates)
    raise FileBrowserError(f"未找到独立 filebrowser skill。已查找：\n  {searched}")


class FileBrowserGateway:
    def __init__(
        self,
        config: UpscaleConfig,
        *,
        scripts_dir: Path | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.config = config
        self.scripts_dir = scripts_dir or discover_filebrowser_scripts(config)
        self.runner = runner

    def _run(self, *args: str) -> dict[str, Any]:
        command = [
            "uv",
            "run",
            "--project",
            str(self.scripts_dir),
            "filebrowser",
            "--non-interactive",
        ]
        if self.config.filebrowser_config:
            command.extend(("--config", str(self.config.filebrowser_config)))
        command.extend(args)
        command.append("--json")
        try:
            process = self.runner(command, text=True, capture_output=True)
        except OSError as exc:
            raise FileBrowserError(f"无法执行 filebrowser CLI: {exc}") from exc
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "未知错误").strip()[-1000:]
            raise FileBrowserError(
                f"filebrowser {args[0]} 失败（退出码 {process.returncode}）：{detail}"
            )
        try:
            payload = json.loads(process.stdout) if process.stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raise FileBrowserError(f"filebrowser {args[0]} 未返回 JSON") from exc
        if not isinstance(payload, dict):
            raise FileBrowserError(f"filebrowser {args[0]} 返回非对象")
        return payload

    def get(
        self,
        remote: str,
        local: Path,
        *,
        source: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        args = ["get", normalize_remote_path(remote), str(local)]
        if source:
            args.extend(("--source", source))
        if dry_run:
            args.append("--dry-run")
        return self._run(*args)

    def put(
        self,
        local: Path,
        remote: str,
        *,
        source: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        args = ["put", str(local), normalize_remote_path(remote)]
        if source:
            args.extend(("--source", source))
        if overwrite:
            args.append("--overwrite")
        return self._run(*args)
