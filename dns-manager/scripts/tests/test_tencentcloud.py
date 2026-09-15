"""Signing invariants and idempotent upsert/delete behavior (no network)."""

import pytest

from dns_manager.models import DnsRecord
from dns_manager.providers.tencentcloud import (
    DEFAULT_LINE,
    TencentCloudDns,
    build_signed_headers,
)

SETTINGS = {"secret_id": "AKIDtest", "secret_key": "testsecret"}


def make_provider(responses, calls):
    """Provider wired to a fake transport recording every API call."""

    def transport(action, payload, secret_id, secret_key):
        calls.append((action, payload))
        return responses(action, payload)

    return TencentCloudDns(SETTINGS, transport=transport)


def record(record_id, value="1.2.3.4", ttl=600, line=DEFAULT_LINE, rtype="A"):
    return {
        "RecordId": record_id,
        "Name": "www",
        "Type": rtype,
        "Value": value,
        "TTL": ttl,
        "Line": line,
        "Status": "ENABLE",
    }


class TestSigning:
    def test_canonical_structure(self):
        body = b'{"Domain":"example.com"}'
        headers = build_signed_headers("CreateRecord", body, 1789440000, "AKIDx", "ks")
        auth = headers["Authorization"]
        assert auth.startswith("TC3-HMAC-SHA256 Credential=AKIDx/2026-09-15/dnspod/tc3_request")
        assert "SignedHeaders=content-type;host;x-tc-action" in auth
        assert len(auth.rsplit("Signature=", 1)[1]) == 64
        assert headers["X-TC-Action"] == "CreateRecord"
        assert headers["X-TC-Version"] == "2021-03-23"

    def test_signature_depends_on_inputs(self):
        body = b'{"Domain":"example.com"}'
        other = b'{"Domain":"other"}'
        base = build_signed_headers("CreateRecord", body, 1789440000, "AKIDx", "ks")
        changed_body = build_signed_headers("CreateRecord", other, 1789440000, "AKIDx", "ks")
        changed_key = build_signed_headers("CreateRecord", body, 1789440000, "AKIDx", "k2")
        changed_action = build_signed_headers("DeleteRecord", body, 1789440000, "AKIDx", "ks")
        sigs = {
            base["Authorization"],
            changed_body["Authorization"],
            changed_key["Authorization"],
            changed_action["Authorization"],
        }
        assert len(sigs) == 4

    def test_deterministic(self):
        body = b'{"Domain":"example.com"}'
        first = build_signed_headers("CreateRecord", body, 1789440000, "AKIDx", "ks")
        second = build_signed_headers("CreateRecord", body, 1789440000, "AKIDx", "ks")
        assert first == second


class TestListRecords:
    def test_maps_and_filters(self):
        calls = []

        def responses(action, payload):
            assert action == "DescribeRecordList"
            return {
                "Response": {
                    "RecordList": [
                        record(1, rtype="A"),
                        record(2, value="example.com.", rtype="CNAME"),
                    ]
                }
            }

        provider = make_provider(responses, calls)
        records = provider.list_records("example.com", subdomain="www", record_type="A")
        assert [r.record_id for r in records] == ["1"]
        assert isinstance(records[0], DnsRecord)
        assert calls[0][1] == {"Domain": "example.com", "Subdomain": "www"}

    def test_empty_zone_error_maps_to_empty_list(self):
        def responses(action, payload):
            return {
                "Response": {
                    "Error": {
                        "Code": "ResourceNotFound.NoDataOfRecord",
                        "Message": "记录列表为空。",
                    }
                }
            }

        provider = make_provider(responses, [])
        assert provider.list_records("empty.com", subdomain="x") == []


class TestUpsert:
    def test_create_when_absent(self):
        calls = []
        state = {"records": []}

        def responses(action, payload):
            if action == "DescribeRecordList":
                if not state["records"]:
                    return {
                        "Response": {
                            "Error": {
                                "Code": "ResourceNotFound.NoDataOfRecord",
                                "Message": "empty",
                            }
                        }
                    }
                return {"Response": {"RecordList": state["records"]}}
            if action == "CreateRecord":
                state["records"] = [record(payload.get("RecordId") or 99, value=payload["Value"])]
                return {"Response": {"RecordId": 99, "RequestId": "r"}}
            raise AssertionError(action)

        provider = make_provider(responses, calls)
        result = provider.upsert_record("example.com", "www", "a", "1.2.3.4", ttl=600)
        assert result.action == "created"
        assert result.record_id == "99"
        create = next(c for c in calls if c[0] == "CreateRecord")[1]
        # DNSPod 要求默认线路用中文"默认"; 英文 "Default" 会被拒绝
        assert create["RecordLine"] == DEFAULT_LINE
        assert create["RecordType"] == "A"
        assert create["TTL"] == 600

    def test_unchanged_when_identical(self):
        def responses(action, payload):
            if action == "DescribeRecordList":
                return {"Response": {"RecordList": [record(7, value="1.2.3.4", ttl=600)]}}
            raise AssertionError(f"write issued for identical record: {action}")

        provider = make_provider(responses, [])
        result = provider.upsert_record("example.com", "www", "A", "1.2.3.4", ttl=600)
        assert result.action == "unchanged"
        assert result.record_id == "7"

    def test_update_when_value_differs(self):
        calls = []

        def responses(action, payload):
            if action == "DescribeRecordList":
                return {"Response": {"RecordList": [record(7, value="9.9.9.9", ttl=600)]}}
            if action == "ModifyRecord":
                assert payload["RecordId"] == 7
                return {"Response": {"RecordId": 7, "RequestId": "r"}}
            raise AssertionError(action)

        provider = make_provider(responses, calls)
        result = provider.upsert_record("example.com", "www", "A", "1.2.3.4")
        assert result.action == "updated"
        assert [c[0] for c in calls] == ["DescribeRecordList", "ModifyRecord"]


class TestDelete:
    def test_delete_matching(self):
        calls = []

        def responses(action, payload):
            if action == "DescribeRecordList":
                return {"Response": {"RecordList": [record(7), record(8, value="5.6.7.8")]}}
            if action == "DeleteRecord":
                return {"Response": {"RequestId": "r"}}
            raise AssertionError(action)

        provider = make_provider(responses, calls)
        result = provider.delete_record("example.com", "www", "A", value="1.2.3.4")
        assert result.action == "deleted"
        assert result.record_ids == ("7",)
        deleted = [c for c in calls if c[0] == "DeleteRecord"]
        assert [c[1]["RecordId"] for c in deleted] == [7]

    def test_delete_absent_is_skipped(self):
        def responses(action, payload):
            return {
                "Response": {
                    "Error": {"Code": "ResourceNotFound.NoDataOfRecord", "Message": "empty"}
                }
            }

        provider = make_provider(responses, [])
        result = provider.delete_record("example.com", "www", "A")
        assert result.action == "skipped"
        assert result.record_ids == ()


class TestProviderInit:
    def test_requires_credentials(self):
        with pytest.raises(Exception, match="secret_id"):
            TencentCloudDns({"secret_id": "", "secret_key": ""}, transport=lambda *a: {})
