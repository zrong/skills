"""Normalized data models shared by all DNS providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DnsRecord:
    """One DNS record as returned by a provider, provider details stripped."""

    record_id: str
    subdomain: str
    type: str
    value: str
    ttl: int
    line: str = ""
    status: str = ""


@dataclass(frozen=True)
class UpsertResult:
    """Outcome of an idempotent create-or-update."""

    action: str  # "created" | "updated" | "unchanged"
    record_id: str
    subdomain: str
    type: str
    value: str
    ttl: int


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of an idempotent delete."""

    action: str  # "deleted" | "skipped"
    record_ids: tuple[str, ...]
    subdomain: str
    type: str
    value: str | None = None
