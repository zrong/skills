"""Upload target adapters."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, TypedDict, cast
from urllib.parse import quote

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .models import ConfigurationError, S3TargetConfig, TargetConfig, UploadResult


class TargetError(RuntimeError):
    """Raised when an upload target rejects or fails a transfer."""


class S3ClientProtocol(Protocol):
    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, str],
        Config: TransferConfig,
    ) -> None: ...


class S3ClientOptions(TypedDict):
    signature_version: str
    s3: dict[str, str]
    retries: dict[str, int | str]
    request_checksum_calculation: Literal["when_required"]
    response_checksum_validation: Literal["when_required"]


class UploadTarget(Protocol):
    name: str

    def resolve_key(self, relative_key: str) -> str: ...

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> None: ...

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str,
    ) -> UploadResult: ...


def normalize_object_key(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise TargetError("S3 object key must be relative and must not contain '..'")
    return "/".join(part for part in path.parts if part not in {"", "."})


def resolve_target_key(config: TargetConfig, relative_key: str) -> str:
    key = normalize_object_key(relative_key)
    match config:
        case S3TargetConfig(prefix=prefix):
            return f"{prefix}/{key}" if prefix else key


def s3_client_options(config: S3TargetConfig) -> S3ClientOptions:
    """Return Boto3 client options compatible with S3 API endpoints."""
    return {
        "signature_version": "s3v4",
        "s3": {"addressing_style": config.addressing_style},
        "retries": {"max_attempts": 3, "mode": "standard"},
        # Avoid optional CRC checksums, which botocore sends as aws-chunked
        # requests that Tencent COS rejects for multipart UploadPart calls.
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
    }


class S3Target:
    """S3-compatible target with overwrite protection and size verification."""

    def __init__(
        self,
        config: S3TargetConfig,
        *,
        client: S3ClientProtocol | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self._client = client or self._build_client(config)
        self._transfer_config = TransferConfig(
            multipart_threshold=config.multipart_threshold_bytes,
            multipart_chunksize=config.multipart_chunksize_bytes,
            max_concurrency=config.max_concurrency,
            use_threads=config.max_concurrency > 1,
        )

    @staticmethod
    def _build_client(config: S3TargetConfig) -> S3ClientProtocol:
        explicit_credentials = config.access_key_id.declared
        access_key = config.access_key_id.resolve(
            f"S3 access key for target {config.name}",
            required=explicit_credentials,
        )
        secret_key = config.secret_access_key.resolve(
            f"S3 secret key for target {config.name}",
            required=explicit_credentials,
        )
        session_token = config.session_token.resolve(
            f"S3 session token for target {config.name}",
            required=config.session_token.declared,
        )

        session = boto3.Session(
            profile_name=config.profile or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            aws_session_token=session_token or None,
            region_name=config.region or None,
        )
        client = cast(
            S3ClientProtocol,
            session.client(  # pyright: ignore[reportUnknownMemberType]
                "s3",
                endpoint_url=config.endpoint_url or None,
                verify=config.verify_tls,
                config=BotoConfig(**cast(Any, s3_client_options(config))),
            ),
        )
        return client

    def resolve_key(self, relative_key: str) -> str:
        return resolve_target_key(self.config, relative_key)

    def _head(self, object_key: str) -> Mapping[str, object] | None:
        try:
            return self._client.head_object(Bucket=self.config.bucket, Key=object_key)
        except ClientError as exc:
            response = cast(Mapping[str, object], exc.response)
            error_value = response.get("Error")
            error: Mapping[str, object]
            if isinstance(error_value, Mapping):
                error = cast(Mapping[str, object], error_value)
            else:
                error = {}
            code = str(error.get("Code", ""))
            status_value = response.get("ResponseMetadata")
            status: Mapping[str, object]
            if isinstance(status_value, Mapping):
                status = cast(Mapping[str, object], status_value)
            else:
                status = {}
            status_code = status.get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status_code == 404:
                return None
            raise TargetError(
                f"S3 head_object failed for target {self.name}: {code or 'unknown'}"
            ) from exc
        except BotoCoreError as exc:
            raise TargetError(f"S3 head_object failed for target {self.name}") from exc

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> None:
        if self._head(object_key) is not None and not overwrite:
            raise TargetError(
                f"S3 object already exists: {self.config.bucket}/{object_key}; "
                "use --overwrite to replace it"
            )

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str,
    ) -> UploadResult:
        extra_args = {
            "ContentType": content_type
            or mimetypes.guess_type(local_path.name)[0]
            or "application/octet-stream"
        }
        if self.config.storage_class:
            extra_args["StorageClass"] = self.config.storage_class
        try:
            self._client.upload_file(
                Filename=str(local_path),
                Bucket=self.config.bucket,
                Key=object_key,
                ExtraArgs=extra_args,
                Config=self._transfer_config,
            )
            metadata = self._head(object_key)
        except (BotoCoreError, ClientError) as exc:
            raise TargetError(f"S3 upload failed for target {self.name}") from exc
        if metadata is None:
            raise TargetError("S3 upload completed but head_object could not find the object")

        local_size = local_path.stat().st_size
        remote_size = metadata.get("ContentLength")
        if not isinstance(remote_size, int) or remote_size != local_size:
            raise TargetError(
                f"S3 size verification failed: local={local_size}, remote={remote_size}"
            )
        etag_value = metadata.get("ETag", "")
        version_value = metadata.get("VersionId", "")
        public_url = (
            f"{self.config.public_base_url}/{quote(object_key, safe='/')}"
            if self.config.public_base_url
            else ""
        )
        return UploadResult(
            target_name=self.name,
            bucket=self.config.bucket,
            object_key=object_key,
            size=local_size,
            etag=str(etag_value).strip('"'),
            version_id=str(version_value),
            public_url=public_url,
        )


def build_target(config: TargetConfig) -> UploadTarget:
    match config:
        case S3TargetConfig():
            return S3Target(config)
    raise ConfigurationError(f"Unsupported target configuration: {type(config).__name__}")
