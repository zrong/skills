"""Upload target adapters."""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast
from urllib.parse import quote

from .models import ConfigurationError, S3TargetConfig, TargetConfig, UploadResult

# boto3/botocore are optional: only needed for the ``upload`` (FileBrowser ->
# S3) command. The file-management surface (``info``/``mkdir``/``update``/
# ``delete``/``move``/``search``/``preview``/``download-files``/``sources``) and
# ``get``/``put`` work without boto3 installed. Install the ``transfer`` extra
# (``uv sync --extra transfer`` or ``pip install filebrowser-transfer[transfer]``)
# to enable S3 transfer.
if TYPE_CHECKING:
    import boto3
    from boto3.s3.transfer import TransferConfig
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import BotoCoreError, ClientError

    _boto3_available = True
else:
    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError

        _boto3_available = True
    except ImportError:  # pragma: no cover - exercised when extras are absent
        boto3 = None  # type: ignore[assignment]
        TransferConfig = None  # type: ignore[assignment,misc]
        BotoConfig = None  # type: ignore[assignment,misc]
        BotoCoreError = Exception
        ClientError = Exception
        _boto3_available = False


class TargetError(RuntimeError):
    """Raised when an upload target rejects or fails a transfer."""


CONTENT_SHA256_METADATA_KEY = "content-sha256"


class S3ClientProtocol(Protocol):
    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]: ...

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, object],
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
        if_changed: bool = False,
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


def calculate_file_sha256(path: Path) -> str:
    """Return the SHA-256 of a staged file without loading it into memory."""
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class S3Target:
    """S3-compatible target with overwrite protection and size verification."""

    def __init__(
        self,
        config: S3TargetConfig,
        *,
        client: S3ClientProtocol | None = None,
    ) -> None:
        if not _boto3_available:
            raise TargetError(
                "S3 transfer requires boto3; install with "
                "`uv sync --extra transfer` (or "
                "`pip install filebrowser-transfer[transfer]`)"
            )
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

    @staticmethod
    def _metadata_content_sha256(metadata: Mapping[str, object]) -> str:
        raw_metadata = metadata.get("Metadata")
        if not isinstance(raw_metadata, Mapping):
            return ""
        typed_metadata = cast(Mapping[object, object], raw_metadata)
        for key, value in typed_metadata.items():
            if str(key).lower() == CONTENT_SHA256_METADATA_KEY and isinstance(value, str):
                return value.lower()
        return ""

    def _result_from_metadata(
        self,
        object_key: str,
        *,
        local_size: int,
        content_sha256: str,
        metadata: Mapping[str, object],
        skipped_unchanged: bool,
    ) -> UploadResult:
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
            skipped_unchanged=skipped_unchanged,
            content_sha256=content_sha256,
        )

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str,
        if_changed: bool = False,
    ) -> UploadResult:
        local_size = local_path.stat().st_size
        content_sha256 = calculate_file_sha256(local_path)
        if if_changed:
            existing = self._head(object_key)
            if (
                existing is not None
                and existing.get("ContentLength") == local_size
                and self._metadata_content_sha256(existing) == content_sha256
            ):
                return self._result_from_metadata(
                    object_key,
                    local_size=local_size,
                    content_sha256=content_sha256,
                    metadata=existing,
                    skipped_unchanged=True,
                )

        extra_args: dict[str, object] = {
            "ContentType": content_type
            or mimetypes.guess_type(local_path.name)[0]
            or "application/octet-stream",
            "Metadata": {CONTENT_SHA256_METADATA_KEY: content_sha256},
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

        return self._result_from_metadata(
            object_key,
            local_size=local_size,
            content_sha256=content_sha256,
            metadata=metadata,
            skipped_unchanged=False,
        )


def build_target(config: TargetConfig) -> UploadTarget:
    match config:
        case S3TargetConfig():
            if not _boto3_available:
                raise TargetError(
                    "S3 transfer requires boto3; install with "
                    "`uv sync --extra transfer` (or "
                    "`pip install filebrowser-transfer[transfer]`)"
                )
            return S3Target(config)
    raise ConfigurationError(f"Unsupported target configuration: {type(config).__name__}")
