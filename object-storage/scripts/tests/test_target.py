from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from object_storage.models import S3TargetConfig
from object_storage.target import (
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
        raw = ExtraArgs.get("Metadata")
        self.metadata[(Bucket, Key)] = (
            {str(key): str(value) for key, value in cast(Mapping[object, object], raw).items()}
            if isinstance(raw, Mapping)
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
        ),
        client=client,
    )


def test_normalize_object_key_and_cos_options() -> None:
    assert normalize_object_key("/folder\\video.mp4") == "folder/video.mp4"
    with pytest.raises(TargetError):
        normalize_object_key("../secret")
    options = s3_client_options(S3TargetConfig(name="cos", bucket="bucket"))
    assert options["request_checksum_calculation"] == "when_required"
    assert options["response_checksum_validation"] == "when_required"


def test_refuses_overwrite_by_default() -> None:
    client = FakeS3Client()
    client.objects[("bucket", "backup/existing.txt")] = b"old"
    target = _target(client)
    with pytest.raises(TargetError, match="already exists"):
        target.ensure_writable("backup/existing.txt", overwrite=False)
    target.ensure_writable("backup/existing.txt", overwrite=True)


def test_upload_writes_digest_and_builds_public_url(tmp_path: Path) -> None:
    client = FakeS3Client()
    local = tmp_path / "hello world.txt"
    local.write_bytes(b"hello")
    target = _target(client)
    key = target.resolve_key("docs/hello world.txt")
    result = target.upload(local, key, content_type="text/plain")

    assert result.object_key == "backup/docs/hello world.txt"
    assert result.public_url.endswith("backup/docs/hello%20world.txt")
    assert result.content_sha256 == calculate_file_sha256(local)
    assert client.extra_args["Metadata"] == {
        CONTENT_SHA256_METADATA_KEY: calculate_file_sha256(local)
    }


def test_if_changed_skips_identical_object(tmp_path: Path) -> None:
    client = FakeS3Client()
    local = tmp_path / "hello.txt"
    local.write_bytes(b"hello")
    target = _target(client)
    key = target.resolve_key("hello.txt")
    client.objects[("bucket", key)] = b"hello"
    client.metadata[("bucket", key)] = {CONTENT_SHA256_METADATA_KEY: calculate_file_sha256(local)}

    result = target.upload(local, key, if_changed=True)
    assert result.skipped_unchanged is True
    assert client.upload_calls == 0


def test_legacy_object_without_digest_is_replaced(tmp_path: Path) -> None:
    client = FakeS3Client()
    local = tmp_path / "hello.txt"
    local.write_bytes(b"hello")
    target = _target(client)
    key = target.resolve_key("hello.txt")
    client.objects[("bucket", key)] = b"hello"

    result = target.upload(local, key, if_changed=True)
    assert result.skipped_unchanged is False
    assert client.upload_calls == 1
