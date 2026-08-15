"""对象存储配置与操作结果模型。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


class ConfigurationError(ValueError):
    """配置值无效。"""


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
class CdnConfig:
    name: str
    provider: str
    base_url: str
    purge_on_upload: bool = False
    access_key_id: SecretValue = field(default_factory=SecretValue, repr=False)
    secret_access_key: SecretValue = field(default_factory=SecretValue, repr=False)


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
    cdn: CdnConfig | None = None


type TargetConfig = S3TargetConfig


@dataclass(frozen=True, slots=True)
class ObjectStorageConfig:
    targets: dict[str, TargetConfig]
    default_target: str

    def target(self, name: str | None = None) -> TargetConfig:
        selected = name or self.default_target
        try:
            return self.targets[selected]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown object storage target: {selected}") from exc


@dataclass(frozen=True, slots=True)
class UploadPlan:
    local_path: str
    target_name: str
    object_key: str
    overwrite: bool
    if_changed: bool


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
