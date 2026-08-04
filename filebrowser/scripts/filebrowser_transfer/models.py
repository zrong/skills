"""Typed configuration and transfer models."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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
class S3TargetConfig:
    name: str
    bucket: str
    region: str = ""
    endpoint_url: str = ""
    public_base_url: str = ""
    prefix: str = ""
    profile: str = ""
    access_key_id: SecretValue = field(default_factory=SecretValue, repr=False)
    secret_access_key: SecretValue = field(default_factory=SecretValue, repr=False)
    session_token: SecretValue = field(default_factory=SecretValue, repr=False)
    addressing_style: Literal["auto", "path", "virtual"] = "auto"
    storage_class: str = ""
    multipart_threshold_bytes: int = 8 * 1024 * 1024
    multipart_chunksize_bytes: int = 8 * 1024 * 1024
    max_concurrency: int = 4
    verify_tls: bool = True


type TargetConfig = S3TargetConfig


@dataclass(frozen=True, slots=True)
class SkillConfig:
    sources: dict[str, FileBrowserSourceConfig]
    targets: dict[str, TargetConfig]
    default_source: str
    default_target: str
    staging_dir: Path | None = None

    def source(self, name: str | None = None) -> FileBrowserSourceConfig:
        selected = name or self.default_source
        try:
            return self.sources[selected]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown FileBrowser source: {selected}") from exc

    def target(self, name: str | None = None) -> TargetConfig:
        selected = name or self.default_target
        try:
            return self.targets[selected]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown upload target: {selected}") from exc


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
class UploadResult:
    target_name: str
    bucket: str
    object_key: str
    size: int
    etag: str
    version_id: str
    public_url: str


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
