"""Typed configuration and transfer models."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when a typed configuration value is invalid."""


@dataclass(frozen=True, slots=True)
class SecretValue:
    direct: str = field(default="", repr=False)
    env_var: str = ""

    def resolve(self, label: str, *, required: bool = True) -> str:
        value = self.direct or (os.environ.get(self.env_var, "") if self.env_var else "")
        if required and not value:
            source = f" or environment variable {self.env_var}" if self.env_var else ""
            raise ConfigurationError(f"Missing {label}{source}")
        return value

    @property
    def configured(self) -> bool:
        return bool(self.direct or (self.env_var and os.environ.get(self.env_var)))

    @property
    def declared(self) -> bool:
        return bool(self.direct or self.env_var)


@dataclass(frozen=True, slots=True)
class FileBrowserSourceConfig:
    name: str
    base_url: str
    token: SecretValue = field(repr=False)
    source: str = "default"
    verify_tls: bool = True
    timeout_seconds: float = 600.0
    max_transfer_bytes: int = 0
    upload_chunk_bytes: int = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SkillConfig:
    sources: dict[str, FileBrowserSourceConfig]
    default_source: str
    staging_dir: Path | None = None

    def source(self, name: str | None = None) -> FileBrowserSourceConfig:
        selected = name or self.default_source
        try:
            return self.sources[selected]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown FileBrowser source: {selected}") from exc


@dataclass(frozen=True, slots=True)
class RemoteFile:
    source: str
    path: str
    name: str
    size: int | None
    content_type: str


@dataclass(frozen=True, slots=True)
class TransferPlan:
    source_name: str
    remote_path: str
    target_name: str
    object_key: str


@dataclass(frozen=True, slots=True)
class CdnTaskResult:
    operation: str
    status: str
    task_id: str
    targets: list[str]
    error: str = ""


@dataclass(frozen=True, slots=True)
class UnchangedFile:
    source_path: str
    object_key: str
    size: int
    content_sha256: str


def _empty_unchanged_files() -> list[UnchangedFile]:
    return []


@dataclass(frozen=True, slots=True)
class UploadResult:
    target_name: str
    bucket: str
    object_key: str
    size: int
    etag: str
    version_id: str
    public_url: str
    cdn_task: CdnTaskResult | None = None
    skipped_unchanged: bool = False
    content_sha256: str = ""
    unchanged_files: list[UnchangedFile] = field(default_factory=_empty_unchanged_files)


@dataclass(frozen=True, slots=True)
class PutPlan:
    source_name: str
    local_path: str
    remote_path: str
    size: int


@dataclass(frozen=True, slots=True)
class PutResult:
    source_name: str
    local_path: str
    remote_path: str
    size: int


@dataclass(frozen=True, slots=True)
class GetPlan:
    source_name: str
    remote_path: str
    local_path: str


@dataclass(frozen=True, slots=True)
class GetResult:
    source_name: str
    remote_path: str
    local_path: str
    size: int
