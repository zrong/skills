"""Provider contract every dns-manager backend must implement.

To add a provider (e.g. aliyun, cloudflare):
1. Create ``dns_manager/providers/<name>.py`` with a ``DnsProvider`` subclass
   whose ``name`` class attribute equals the TOML provider key.
2. Register it in ``dns_manager/providers/__init__.py``.
3. Add the matching ``[dns-manager.<name>]`` block to agent_config.example.toml
   and references/configuration.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..models import DeleteResult, DnsRecord, UpsertResult


class DnsProviderError(Exception):
    """Provider call failed after retries; message is safe to show the user."""


class DnsProvider(ABC):
    """Normalized record operations. All methods must be idempotent."""

    name: ClassVar[str]

    def __init__(self, settings: dict):
        self.settings = settings

    @abstractmethod
    def list_records(
        self,
        domain: str,
        subdomain: str | None = None,
        record_type: str | None = None,
    ) -> list[DnsRecord]:
        """List records; empty filters return every record of the zone."""

    @abstractmethod
    def upsert_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        value: str,
        ttl: int = 600,
        line: str | None = None,
    ) -> UpsertResult:
        """Create the record, or update it when one already exists.

        Re-running with identical subdomain/type/value/ttl must report
        ``action="unchanged"`` without issuing a write.
        """

    @abstractmethod
    def delete_record(
        self,
        domain: str,
        subdomain: str,
        record_type: str,
        value: str | None = None,
    ) -> DeleteResult:
        """Delete matching records; absent records report ``action="skipped"``."""
