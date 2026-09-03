"""User-facing error types."""


class MattingError(RuntimeError):
    """Base error for deterministic CLI failures."""


class ConfigurationError(MattingError):
    """Configuration is missing or invalid."""


class ServiceUnavailableError(MattingError):
    """The configured service cannot currently be used."""


class ApiError(MattingError):
    """The service rejected or failed an operation."""
