"""S3 兼容对象存储适配器。"""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, TypedDict, cast
from urllib.parse import quote
from uuid import uuid4

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .models import DownloadResult, ObjectHeadResult, RemoteObject, S3TargetConfig, UploadResult


class TargetError(RuntimeError):
    """对象存储拒绝或无法完成操作。"""


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

    def download_file(
        self,
        Bucket: str,
        Key: str,
        Filename: str,
        Config: TransferConfig,
    ) -> None: ...

    def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str,
        ContinuationToken: str | None = None,
    ) -> Mapping[str, object]: ...


class S3ClientOptions(TypedDict):
    signature_version: str
    s3: dict[str, str]
    retries: dict[str, int | str]
    request_checksum_calculation: Literal["when_required"]
    response_checksum_validation: Literal["when_required"]


def normalize_object_key(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise TargetError("S3 object key must be relative and must not contain '..'")
    return "/".join(part for part in path.parts if part not in {"", "."})


def resolve_target_key(config: S3TargetConfig, relative_key: str) -> str:
    key = normalize_object_key(relative_key)
    return f"{config.prefix}/{key}" if config.prefix else key


def s3_client_options(config: S3TargetConfig) -> S3ClientOptions:
    """返回兼容 S3 API 端点和腾讯云 COS 的 botocore 参数。"""
    return {
        "signature_version": "s3v4",
        "s3": {"addressing_style": config.addressing_style},
        "retries": {"max_attempts": 3, "mode": "standard"},
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
    }


def calculate_file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class S3Target:
    """带覆盖保护、内容摘要和上传后尺寸校验的 S3 目标。"""

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
            f"S3 access key for target {config.name}", required=explicit_credentials
        )
        secret_key = config.secret_access_key.resolve(
            f"S3 secret key for target {config.name}", required=explicit_credentials
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
        client = cast(Any, session).client(
            "s3",
            endpoint_url=config.endpoint_url or None,
            verify=config.verify_tls,
            config=BotoConfig(**cast(Any, s3_client_options(config))),
        )
        return cast(S3ClientProtocol, client)

    def resolve_key(self, relative_key: str) -> str:
        return resolve_target_key(self.config, relative_key)

    def _head(self, object_key: str) -> Mapping[str, object] | None:
        try:
            return self._client.head_object(Bucket=self.config.bucket, Key=object_key)
        except ClientError as exc:
            response = cast(Mapping[str, object], exc.response)
            raw_error = response.get("Error")
            error: Mapping[str, object] = (
                cast(Mapping[str, object], raw_error) if isinstance(raw_error, Mapping) else {}
            )
            raw_metadata = response.get("ResponseMetadata")
            metadata: Mapping[str, object] = (
                cast(Mapping[str, object], raw_metadata)
                if isinstance(raw_metadata, Mapping)
                else {}
            )
            code = str(error.get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"} or metadata.get("HTTPStatusCode") == 404:
                return None
            raise TargetError(
                f"S3 head_object failed for target {self.name}: {code or 'unknown'}"
            ) from exc
        except BotoCoreError as exc:
            raise TargetError(f"S3 head_object failed for target {self.name}") from exc

    def ensure_writable(self, object_key: str, *, overwrite: bool) -> Mapping[str, object] | None:
        existing = self._head(object_key)
        if existing is not None and not overwrite:
            raise TargetError(
                f"S3 object already exists: {self.config.bucket}/{object_key}; "
                "use --overwrite to replace it"
            )
        return existing

    def list_prefix(self, object_prefix: str) -> list[RemoteObject]:
        prefix = f"{normalize_object_key(object_prefix).rstrip('/')}/"
        continuation_token: str | None = None
        objects: list[RemoteObject] = []
        while True:
            request: dict[str, str] = {"Bucket": self.config.bucket, "Prefix": prefix}
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            try:
                response = self._client.list_objects_v2(**request)
            except (BotoCoreError, ClientError) as exc:
                raise TargetError(f"S3 list_objects_v2 failed for target {self.name}") from exc
            raw_contents = response.get("Contents", [])
            if not isinstance(raw_contents, list):
                raise TargetError("S3 list_objects_v2 returned invalid contents")
            for item in cast(list[object], raw_contents):
                if not isinstance(item, Mapping):
                    raise TargetError("S3 list_objects_v2 returned invalid object")
                record = cast(Mapping[str, object], item)
                key = record.get("Key")
                size = record.get("Size")
                if not isinstance(key, str) or not isinstance(size, int):
                    raise TargetError("S3 list_objects_v2 returned invalid key or size")
                if key.endswith("/"):
                    continue
                objects.append(RemoteObject(object_key=key, size=size))
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                raise TargetError("S3 list_objects_v2 did not return a continuation token")
            continuation_token = token
        return objects

    def head(self, object_key: str) -> ObjectHeadResult:
        metadata = self._head(object_key)
        if metadata is None:
            raise TargetError(f"S3 object does not exist: {self.config.bucket}/{object_key}")
        size = metadata.get("ContentLength")
        if not isinstance(size, int):
            raise TargetError("S3 head_object did not return object size")
        content_type = metadata.get("ContentType")
        cache_control = metadata.get("CacheControl")
        return ObjectHeadResult(
            target_name=self.name,
            bucket=self.config.bucket,
            object_key=object_key,
            size=size,
            content_type=content_type if isinstance(content_type, str) else "",
            cache_control=cache_control if isinstance(cache_control, str) else "",
            etag=str(metadata.get("ETag", "")).strip('"'),
            version_id=str(metadata.get("VersionId", "")),
            content_sha256=self._metadata_content_sha256(metadata),
        )

    @staticmethod
    def _metadata_content_sha256(metadata: Mapping[str, object]) -> str:
        raw = metadata.get("Metadata")
        if not isinstance(raw, Mapping):
            return ""
        for key, value in cast(Mapping[object, object], raw).items():
            if str(key).lower() == CONTENT_SHA256_METADATA_KEY and isinstance(value, str):
                return value.lower()
        return ""

    def _result(
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
            etag=str(metadata.get("ETag", "")).strip('"'),
            version_id=str(metadata.get("VersionId", "")),
            public_url=public_url,
            skipped_unchanged=skipped_unchanged,
            content_sha256=content_sha256,
            cache_control=str(metadata.get("CacheControl", "")),
        )

    def upload(
        self,
        local_path: Path,
        object_key: str,
        *,
        content_type: str = "",
        if_changed: bool = False,
        cache_control: str | None = None,
        existing: Mapping[str, object] | None = None,
        existing_checked: bool = False,
    ) -> UploadResult:
        local_size = local_path.stat().st_size
        content_sha256 = calculate_file_sha256(local_path)
        if not existing_checked and (if_changed or cache_control is None):
            existing = self._head(object_key)
        if (
            if_changed
            and existing is not None
            and existing.get("ContentLength") == local_size
            and self._metadata_content_sha256(existing) == content_sha256
            and (cache_control is None or existing.get("CacheControl") == cache_control)
        ):
            return self._result(
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
        selected_cache_control = cache_control
        if selected_cache_control is None and existing is not None:
            old_cache_control = existing.get("CacheControl")
            if isinstance(old_cache_control, str) and old_cache_control:
                selected_cache_control = old_cache_control
        if selected_cache_control is not None:
            extra_args["CacheControl"] = selected_cache_control
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
        return self._result(
            object_key,
            local_size=local_size,
            content_sha256=content_sha256,
            metadata=metadata,
            skipped_unchanged=False,
        )

    def download(self, object_key: str, output_path: Path) -> DownloadResult:
        metadata = self._head(object_key)
        if metadata is None:
            raise TargetError(f"S3 object does not exist: {self.config.bucket}/{object_key}")
        remote_size = metadata.get("ContentLength")
        if not isinstance(remote_size, int):
            raise TargetError("S3 download could not determine object size")
        expected_sha256 = self._metadata_content_sha256(metadata)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.part")
        try:
            self._client.download_file(
                Bucket=self.config.bucket,
                Key=object_key,
                Filename=str(temporary_path),
                Config=self._transfer_config,
            )
            local_size = temporary_path.stat().st_size
            if local_size != remote_size:
                raise TargetError(
                    "S3 download size verification failed: "
                    f"local={local_size}, remote={remote_size}"
                )
            actual_sha256 = calculate_file_sha256(temporary_path)
            if expected_sha256 and actual_sha256 != expected_sha256:
                raise TargetError("S3 download SHA-256 verification failed")
            temporary_path.replace(output_path)
        except TargetError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise TargetError(f"S3 download failed for target {self.name}") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return DownloadResult(
            target_name=self.name,
            bucket=self.config.bucket,
            object_key=object_key,
            output_path=str(output_path),
            size=remote_size,
            content_sha256=actual_sha256,
            sha256_verified=bool(expected_sha256),
        )
