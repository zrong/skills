"""通过稳定 CLI/JSON 契约调用独立 object-storage Skill。"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast


class ObjectStorageError(RuntimeError):
    """object-storage Skill 不可用或返回无效结果。"""


class ObjectStorageGateway(Protocol):
    def summary(self) -> dict[str, object]: ...

    def resolve_key(self, key: str, *, target_name: str | None = None) -> dict[str, object]: ...

    def upload(
        self,
        local_path: Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
        content_type: str = "",
    ) -> dict[str, object]: ...

    def cdn(
        self,
        command: str,
        *,
        target_name: str | None,
        urls: list[str] | None,
        keys: list[str] | None,
        flush_type: str = "flush",
        area: str = "",
        dry_run: bool = False,
    ) -> dict[str, object]: ...


def _scripts_dir(candidate: Path) -> Path | None:
    expanded = candidate.expanduser().resolve()
    scripts = expanded if expanded.name == "scripts" else expanded / "scripts"
    return scripts if (scripts / "pyproject.toml").is_file() else None


def find_object_storage_scripts() -> Path:
    """查找可独立运行的 object-storage Skill scripts 目录。"""
    candidates: list[Path] = []
    if configured := os.environ.get("OBJECT_STORAGE_SKILL_DIR"):
        candidates.append(Path(configured))
    cwd = Path.cwd().resolve()
    for ancestor in (cwd, *cwd.parents):
        candidates.extend(
            [
                ancestor / "skills" / "object-storage",
                ancestor / ".agents" / "skills" / "object-storage",
                ancestor / ".claude" / "skills" / "object-storage",
            ]
        )
    candidates.extend(
        [
            Path.home() / ".agents" / "skills" / "object-storage",
            Path.home() / ".claude" / "skills" / "object-storage",
            Path(__file__).resolve().parents[3] / "object-storage",
        ]
    )
    for candidate in candidates:
        if scripts := _scripts_dir(candidate):
            return scripts
    searched = ", ".join(str(candidate.expanduser()) for candidate in candidates)
    raise ObjectStorageError(
        "object-storage Skill is not installed. Install it beside filebrowser or set "
        f"OBJECT_STORAGE_SKILL_DIR. Searched: {searched}"
    )


class CliObjectStorageGateway:
    def __init__(
        self,
        config_path: Path | None,
        *,
        scripts_dir: Path | None = None,
    ) -> None:
        self.config_path = config_path
        self.scripts_dir = scripts_dir or find_object_storage_scripts()

    def _run(self, arguments: Sequence[str]) -> dict[str, object]:
        command = [
            "uv",
            "run",
            "--project",
            str(self.scripts_dir),
            "object-storage",
            "--non-interactive",
        ]
        if self.config_path is not None:
            command.extend(["--config", str(self.config_path)])
        command.extend(arguments)
        command.append("--json")
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise ObjectStorageError(f"object-storage command failed: {detail}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ObjectStorageError("object-storage returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ObjectStorageError("object-storage JSON result must be an object")
        return cast(dict[str, object], payload)

    def summary(self) -> dict[str, object]:
        return self._run(["list"])

    def resolve_key(self, key: str, *, target_name: str | None = None) -> dict[str, object]:
        arguments = ["resolve-key", key]
        if target_name:
            arguments.extend(["--target", target_name])
        return self._run(arguments)

    def upload(
        self,
        local_path: Path,
        *,
        target_name: str | None = None,
        object_key: str | None = None,
        overwrite: bool = False,
        if_changed: bool = False,
        content_type: str = "",
    ) -> dict[str, object]:
        arguments = ["upload", str(local_path)]
        if target_name:
            arguments.extend(["--target", target_name])
        if object_key:
            arguments.extend(["--key", object_key])
        if content_type:
            arguments.extend(["--content-type", content_type])
        if overwrite:
            arguments.append("--overwrite")
        if if_changed:
            arguments.append("--if-changed")
        return self._run(arguments)

    def cdn(
        self,
        command: str,
        *,
        target_name: str | None,
        urls: list[str] | None,
        keys: list[str] | None,
        flush_type: str = "flush",
        area: str = "",
        dry_run: bool = False,
    ) -> dict[str, object]:
        arguments = ["cdn", command]
        if target_name:
            arguments.extend(["--target", target_name])
        if urls:
            arguments.extend(["--urls", *urls])
        elif keys:
            arguments.extend(["--keys", *keys])
        else:
            raise ObjectStorageError("CDN command requires urls or keys")
        if command == "purge-path":
            arguments.extend(["--flush-type", flush_type])
        if command == "prefetch" and area:
            arguments.extend(["--area", area])
        if dry_run:
            arguments.append("--dry-run")
        return self._run(arguments)


def payload_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ObjectStorageError(f"object-storage result field {key} must be a string")
    return value


def payload_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ObjectStorageError(f"object-storage result field {key} must be an integer")
    return value


def payload_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ObjectStorageError(f"object-storage result field {key} must be a boolean")
    return value
