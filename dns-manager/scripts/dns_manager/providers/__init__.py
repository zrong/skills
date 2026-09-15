"""Provider registry. New providers register here to become configurable."""

from __future__ import annotations

from .base import DnsProvider, DnsProviderError
from .tencentcloud import TencentCloudDns

_REGISTRY: dict[str, type[DnsProvider]] = {
    TencentCloudDns.name: TencentCloudDns,
}

PROVIDER_NAMES = frozenset(_REGISTRY)


def get_provider(name: str, settings: dict) -> DnsProvider:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        supported = ", ".join(sorted(_REGISTRY))
        raise DnsProviderError(
            f"Unsupported provider {name!r}; supported: {supported}"
        ) from None
    return cls(settings)


__all__ = [
    "PROVIDER_NAMES",
    "DnsProvider",
    "DnsProviderError",
    "get_provider",
]
