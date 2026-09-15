from __future__ import annotations

import json
from typing import Any, cast

import boto3
import pytest
from botocore.stub import Stubber

from object_storage.cli import main
from object_storage.cloudfront import CloudFrontCacheManager
from object_storage.config import ConfigError, parse_skill_config
from object_storage.models import CdnConfig, ConfigurationError, S3TargetConfig


def manager(client: Any = None) -> CloudFrontCacheManager:
    return CloudFrontCacheManager(
        S3TargetConfig(
            name="aws",
            bucket="bucket",
            cdn=CdnConfig(
                name="aws",
                provider="cloudfront",
                base_url="https://cdn.test",
                distribution_id="DIST",
            ),
        ),
        client=client,
    )


def test_paths_and_directory_scope() -> None:
    m = manager()
    assert m.invalidation_paths(["https://cdn.test/中 文?a=1", "https://cdn.test/中 文?a=1"]) == [
        "/%E4%B8%AD%20%E6%96%87?a=1"
    ]
    assert m.invalidation_paths(["https://cdn.test/dir"], directory=True) == ["/dir/*"]
    assert m.invalidation_paths(["https://cdn.test/"], directory=True) == ["/*"]
    with pytest.raises(ConfigurationError):
        m.invalidation_paths(["https://other.test/a"])
    with pytest.raises(ConfigurationError):
        m.prefetch(["https://cdn.test/a"])


def test_submit_and_status() -> None:
    class Client:
        def create_invalidation(self, **kwargs: Any) -> Any:
            assert kwargs["DistributionId"] == "DIST"
            assert kwargs["InvalidationBatch"]["Paths"] == {"Quantity": 1, "Items": ["/dir/*"]}
            assert kwargs["InvalidationBatch"]["CallerReference"]
            return {"Invalidation": {"Id": "I123"}}

        def get_invalidation(self, **kwargs: Any) -> Any:
            assert kwargs == {"DistributionId": "DIST", "Id": "I123"}
            return {
                "Invalidation": {
                    "Id": "I123",
                    "Status": "Completed",
                    "InvalidationBatch": {"Paths": {"Items": ["/dir/*"]}},
                }
            }

    m = manager(Client())
    assert m.purge_path(["https://cdn.test/dir"], flush_type="flush").status == "submitted"
    assert m.status("I123").status == "completed"


def test_access_denied_redacts_sdk_message() -> None:
    client = cast(Any, boto3).client(
        "cloudfront", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stub:
        stub.add_client_error("create_invalidation", "AccessDenied", "sensitive text")
        result = manager(client).purge_url(["https://cdn.test/a"])
    assert result.status == "failed"
    assert result.error == "AccessDenied"


def test_config_requires_id_and_allows_profile() -> None:
    doc: Any = {
        "object-storage": {
            "targets": {
                "aws": {
                    "bucket": "bucket",
                    "profile": "aws",
                    "cdn": {
                        "provider": "cloudfront",
                        "base_url": "https://cdn.test",
                    },
                }
            }
        }
    }
    with pytest.raises(ConfigError, match="distribution_id"):
        parse_skill_config(doc)
    doc["object-storage"]["targets"]["aws"]["cdn"]["distribution_id"] = "DIST"
    assert parse_skill_config(doc).target().profile == "aws"


def test_cli_paths_dry_run(tmp_path: Any, capsys: Any) -> None:
    config = tmp_path / "agent_config.toml"
    config.write_text("""[object-storage.targets.aws]
bucket = "bucket"
[object-storage.targets.aws.cdn]
provider = "cloudfront"
base_url = "https://cdn.test"
distribution_id = "DIST"
""")
    assert (
        main(
            [
                "--config",
                str(config),
                "cdn",
                "purge-path",
                "--paths",
                "https://cdn.test/dir",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["targets"] == ["/dir/*"]
