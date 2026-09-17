class UpscaleError(RuntimeError):
    """Base user-actionable error."""


class ConfigurationError(UpscaleError):
    """Invalid or missing configuration."""


class ServiceUnavailableError(UpscaleError):
    """The configured API cannot currently be used."""


class ApiError(UpscaleError):
    """The API violated its contract or rejected an operation."""


class FileBrowserError(UpscaleError):
    """The external filebrowser skill failed."""
