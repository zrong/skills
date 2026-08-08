from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from filebrowser_transfer.models import ConfigurationError, S3TargetConfig, SecretValue
from filebrowser_transfer.targets import (
    CONTENT_SHA256_METADATA_KEY,
    S3Target,
    TargetError,
    calculate_file_sha256,
    normalize_object_key,
    s3_client_options,
)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.extra_args: dict[str, object] = {}
        self.upload_calls = 0

    def head_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:
        try:
            content = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {
                        "RequestId": "request-id",
                        "HostId": "host-id",
                        "HTTPStatusCode": 404,
                        "HTTPHeaders": {},
                        "RetryAttempts": 0,
                    },
                },
                "HeadObject",
            ) from exc
        return {
            "ContentLength": len(content),
            "ETag": '"etag-value"',
            "Metadata": self.metadata.get((Bucket, Key), {}),
        }

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: dict[str, object],
        Config: TransferConfig,
    ) -> None:
        del Config
        self.upload_calls += 1
        self.objects[(Bucket, Key)] = Path(Filename).read_bytes()
        raw_metadata = ExtraArgs.get("Metadata")
        self.metadata[(Bucket, Key)] = (
            {
                str(key): str(value)
                for key, value in cast(Mapping[object, object], raw_metadata).items()
            }
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        self.extra_args = ExtraArgs


def _target(client: FakeS3Client) -> S3Target:
    return S3Target(
        S3TargetConfig(
            name="archive",
            bucket="bucket",
            prefix="backup",
            public_base_url="https://cdn.example.test",
            storage_class="STANDARD_IA",
        ),
        client=client,
    )


def test_normalize_object_key() -> None:
    assert normalize_object_key("/folder\\video.mp4") == "folder/video.mp4"
    with pytest.raises(TargetError):
        normalize_object_key("../secret")


def test_s3_client_config_avoids_optional_aws_chunked_checksums() -> None:
    client_options = s3_client_options(
        S3TargetConfig(name="cos", bucket="bucket", addressing_style="virtual")
    )

    assert client_options["s3"] == {"addressing_style": "virtual"}
    assert client_options["request_checksum_calculation"] == "when_required"
    assert client_options["response_checksum_validation"] == "when_required"


def test_refuses_overwrite_by_default() -> None:
    client = FakeS3Client()
    client.objects[("bucket", "backup/existing.txt")] = b"old"
    target = _target(client)
    with pytest.raises(TargetError, match="already exists"):
        target.ensure_writable("backup/existing.txt", overwrite=False)
    target.ensure_writable("backup/existing.txt", overwrite=True)


def test_upload_verifies_size_and_builds_public_url(tmp_path: Path) -> None:
    client = FakeS3Client()
    target = _target(client)
    local_path = tmp_path / "hello world.txt"
    local_path.write_bytes(b"hello")
    key = target.resolve_key("docs/hello world.txt")
    target.ensure_writable(key, overwrite=False)
    result = target.upload(local_path, key, content_type="text/plain")

    assert result.object_key == "backup/docs/hello world.txt"
    assert result.size == 5
    assert result.etag == "etag-value"
    assert result.public_url == "https://cdn.example.test/backup/docs/hello%20world.txt"
    assert result.content_sha256 == calculate_file_sha256(local_path)
    assert result.skipped_unchanged is False
    assert client.extra_args == {
        "ContentType": "text/plain",
        "StorageClass": "STANDARD_IA",
        "Metadata": {CONTENT_SHA256_METADATA_KEY: calculate_file_sha256(local_path)},
    }


def test_upload_skips_identical_existing_object_when_if_changed(tmp_path: Path) -> None:
    client = FakeS3Client()
    target = _target(client)
    local_path = tmp_path / "hello.txt"
    local_path.write_bytes(b"hello")
    key = target.resolve_key("docs/hello.txt")
    client.objects[("bucket", key)] = b"hello"
    client.metadata[("bucket", key)] = {
        CONTENT_SHA256_METADATA_KEY: calculate_file_sha256(local_path)
    }

    result = target.upload(local_path, key, content_type="text/plain", if_changed=True)

    assert result.skipped_unchanged is True
    assert result.content_sha256 == calculate_file_sha256(local_path)
    assert client.upload_calls == 0


def test_upload_replaces_legacy_object_without_content_digest(tmp_path: Path) -> None:
    client = FakeS3Client()
    target = _target(client)
    local_path = tmp_path / "hello.txt"
    local_path.write_bytes(b"hello")
    key = target.resolve_key("docs/hello.txt")
    client.objects[("bucket", key)] = b"hello"

    result = target.upload(local_path, key, content_type="text/plain", if_changed=True)

    assert result.skipped_unchanged is False
    assert client.upload_calls == 1
    assert client.metadata[("bucket", key)] == {
        CONTENT_SHA256_METADATA_KEY: calculate_file_sha256(local_path)
    }


def test_declared_environment_credentials_must_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MISSING_S3_SECRET_KEY", raising=False)
    config = S3TargetConfig(
        name="archive",
        bucket="bucket",
        access_key_id=SecretValue(env_var="MISSING_S3_ACCESS_KEY"),
        secret_access_key=SecretValue(env_var="MISSING_S3_SECRET_KEY"),
    )
    with pytest.raises(ConfigurationError, match="Missing S3 access key"):
        S3Target(config)
