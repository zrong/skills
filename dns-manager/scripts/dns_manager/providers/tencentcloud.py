"""Tencent Cloud DNSPod provider via the OpenAPI (dnspod 2021-03-23).

Uses hand-rolled TC3-HMAC-SHA256 signing with the standard library only, so
the skill stays dependency-free. All record operations are idempotent.

Two API quirks baked in from production use:
- ``RecordLine`` must be the Chinese string ``默认`` for the default line
  (the English "Default" is rejected by CreateRecord).
- DescribeRecordList answers an empty zone with error code
  ``ResourceNotFound.NoDataOfRecord``, which maps to an empty list here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from ..models import DeleteResult, DnsRecord, UpsertResult
from .base import DnsProvider, DnsProviderError

HOST = "dnspod.tencentcloudapi.com"
SERVICE = "dnspod"
API_VERSION = "2021-03-23"
DEFAULT_LINE = "默认"
DEFAULT_TTL = 600

Transport = Callable[[str, dict, str, str], dict]


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def build_signed_headers(
    action: str,
    body: bytes,
    timestamp: int,
    secret_id: str,
    secret_key: str,
) -> dict[str, str]:
    """Build request headers carrying a TC3-HMAC-SHA256 signature."""
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    hashed_payload = hashlib.sha256(body).hexdigest()
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            "content-type:application/json; charset=utf-8",
            f"host:{HOST}",
            f"x-tc-action:{action.lower()}",
            "",
            "content-type;host;x-tc-action",
            hashed_payload,
        ]
    )
    credential_scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        "SignedHeaders=content-type;host;x-tc-action, "
        f"Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": API_VERSION,
        "Host": HOST,
    }


def urllib_transport(action: str, payload: dict, secret_id: str, secret_key: str) -> dict:
    """Default transport: signed POST over HTTPS. Never logs secrets."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = build_signed_headers(action, body, int(time.time()), secret_id, secret_key)
    request = urllib.request.Request(
        f"https://{HOST}/", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # API errors usually arrive as HTTP 200 bodies; non-200 means the
        # request never reached the business layer.
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise DnsProviderError(f"Tencent Cloud API HTTP {exc.code}: {detail}") from exc


def _unwrap(response: dict, action: str) -> dict:
    body = response.get("Response", response)
    error = body.get("Error")
    if error:
        raise DnsProviderError(
            f"{action} failed: {error.get('Code')}: {error.get('Message')}"
        )
    return body


class TencentCloudDns(DnsProvider):
    name = "tencentcloud"

    def __init__(self, settings: dict, transport: Transport | None = None):
        super().__init__(settings)
        self._transport = transport or urllib_transport
        self._secret_id = settings.get("secret_id", "")
        self._secret_key = settings.get("secret_key", "")
        if not self._secret_id or not self._secret_key:
            raise DnsProviderError(
                "tencentcloud requires secret_id and secret_key "
                "(set [dns-manager.tencentcloud] or TENCENTCLOUD_SECRET_ID/KEY)"
            )

    def _call(self, action: str, payload: dict) -> dict:
        return _unwrap(
            self._transport(action, payload, self._secret_id, self._secret_key), action
        )

    def list_records(
        self,
        domain: str,
        subdomain: str | None = None,
        record_type: str | None = None,
    ) -> list[DnsRecord]:
        params: dict = {"Domain": domain}
        if subdomain:
            params["Subdomain"] = subdomain
        try:
            body = self._call("DescribeRecordList", params)
        except DnsProviderError as exc:
            if "ResourceNotFound.NoDataOfRecord" in str(exc):
                return []
            raise
        return [
            DnsRecord(
                record_id=str(item["RecordId"]),
                subdomain=item.get("Name", ""),
                type=item.get("Type", ""),
                value=item.get("Value", ""),
                ttl=int(item.get("TTL", 0) or 0),
                line=item.get("Line", ""),
                status=item.get("Status", ""),
            )
            for item in body.get("RecordList") or []
            if not record_type or item.get("Type") == record_type.upper()
        ]

    def upsert_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        value: str,
        ttl: int = DEFAULT_TTL,
        line: str | None = None,
    ) -> UpsertResult:
        record_type = record_type.upper()
        line = line or DEFAULT_LINE
        existing = [
            r for r in self.list_records(domain, subdomain, record_type)
            if r.line == line
        ]
        base = {
            "Domain": domain,
            "SubDomain": subdomain,
            "RecordType": record_type,
            "RecordLine": line,
            "Value": value,
            "TTL": ttl,
        }
        if existing:
            match = existing[0]
            if match.value == value and match.ttl in (0, ttl):
                return UpsertResult(
                    "unchanged", match.record_id, subdomain, record_type, value, ttl
                )
            body = self._call("ModifyRecord", {"RecordId": int(match.record_id), **base})
            record_id = str(body.get("RecordId") or match.record_id)
            return UpsertResult("updated", record_id, subdomain, record_type, value, ttl)
        body = self._call("CreateRecord", base)
        record_id = str(body.get("RecordId") or "")
        if not record_id:
            raise DnsProviderError(f"CreateRecord returned no RecordId: {body}")
        return UpsertResult("created", record_id, subdomain, record_type, value, ttl)

    def delete_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        value: str | None = None,
    ) -> DeleteResult:
        record_type = record_type.upper()
        matches = [
            r
            for r in self.list_records(domain, subdomain, record_type)
            if value is None or r.value == value
        ]
        if not matches:
            return DeleteResult("skipped", (), subdomain, record_type, value)
        deleted: list[str] = []
        for record in matches:
            self._call(
                "DeleteRecord", {"Domain": domain, "RecordId": int(record.record_id)}
            )
            deleted.append(record.record_id)
        return DeleteResult("deleted", tuple(deleted), subdomain, record_type, value)
