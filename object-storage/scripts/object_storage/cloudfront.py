"""CloudFront invalidation adapter; credentials come from the S3 target."""

from __future__ import annotations

from typing import Any, Literal, cast
from urllib.parse import quote, urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .cdn import build_cdn_url
from .models import CdnTaskResult, ConfigurationError, S3TargetConfig


class CloudFrontCacheManager:
    def __init__(self, target: S3TargetConfig, *, client: Any = None) -> None:
        if target.cdn is None or not target.cdn.distribution_id:
            raise ConfigurationError("CloudFront requires distribution_id")
        self.config = target.cdn
        self.target = target
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            t = self.target
            explicit = t.access_key_id.declared
            session = boto3.Session(
                profile_name=t.profile or None,
                aws_access_key_id=t.access_key_id.resolve("AWS access key", required=explicit)
                or None,
                aws_secret_access_key=t.secret_access_key.resolve(
                    "AWS secret key", required=explicit
                )
                or None,
                aws_session_token=t.session_token.resolve(
                    "AWS session token", required=t.session_token.declared
                )
                or None,
                region_name=t.region or "us-east-1",
            )
            self._client = cast(Any, session).client(
                "cloudfront",
                verify=t.verify_tls,
                config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}),
            )
        return self._client

    def build_url(self, object_key: str) -> str:
        return build_cdn_url(self.config.base_url, object_key)

    def invalidation_paths(self, urls: list[str], *, directory: bool = False) -> list[str]:
        result: list[str] = []
        base = urlsplit(self.config.base_url)
        for url in urls:
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                raise ConfigurationError("CloudFront URL must match configured cdn.base_url host")
            if parsed.fragment:
                raise ConfigurationError("CloudFront invalidation URL cannot contain a fragment")
            path = quote(parsed.path or "/", safe="/%:@!$&'()+,;=-._*")
            if directory:
                if parsed.query or "*" in path:
                    raise ConfigurationError("Directory URL cannot contain a query or wildcard")
                path = path.rstrip("/") + "/*"
            elif parsed.query:
                path += "?" + quote(parsed.query, safe="%=&;+,:/@?*-._")
            if len(path) > 4000 or "~" in path or "%7e" in path.lower():
                raise ConfigurationError("Unsupported CloudFront invalidation path")
            if path not in result:
                result.append(path)
        if not result:
            raise ConfigurationError("At least one CloudFront invalidation path is required")
        return result

    @staticmethod
    def _failed(operation: str, targets: list[str], exc: Exception) -> CdnTaskResult:
        # Never echo SDK exception text, URLs or credentials.
        code = (
            str(exc.response.get("Error", {}).get("Code", "ClientError"))
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        return CdnTaskResult(operation, "failed", "", targets, error=code)

    def _invalidate(self, operation: str, paths: list[str]) -> CdnTaskResult:
        try:
            response = self._ensure_client().create_invalidation(
                DistributionId=self.config.distribution_id,
                InvalidationBatch={
                    "CallerReference": str(uuid4()),
                    "Paths": {"Quantity": len(paths), "Items": paths},
                },
            )
            return CdnTaskResult(operation, "submitted", response["Invalidation"]["Id"], paths)
        except Exception as exc:
            return self._failed(operation, paths, exc)

    def purge_url(self, urls: list[str]) -> CdnTaskResult:
        return self._invalidate("purge_url", self.invalidation_paths(urls))

    def purge_path(
        self, paths: list[str], *, flush_type: Literal["flush", "delete"]
    ) -> CdnTaskResult:
        return self._invalidate("purge_path", self.invalidation_paths(paths, directory=True))

    def prefetch(self, urls: list[str], *, area: str = "") -> CdnTaskResult:
        raise ConfigurationError("CloudFront does not support CDN prefetch")

    def status(self, task_id: str) -> CdnTaskResult:
        try:
            item = self._ensure_client().get_invalidation(
                DistributionId=self.config.distribution_id, Id=task_id
            )["Invalidation"]
            return CdnTaskResult(
                "status",
                "completed" if item["Status"] == "Completed" else "in_progress",
                item["Id"],
                item["InvalidationBatch"]["Paths"].get("Items", []),
            )
        except Exception as exc:
            return self._failed("status", [], exc)
