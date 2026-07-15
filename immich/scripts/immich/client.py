"""Immich API client."""

import asyncio
import httpx
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from immich.config import get_api_key, get_base_url, normalize_base_url


class ImmichClient:
    """Client for Immich API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool = True,
    ):
        from immich.config import get_immich_config, load_config

        # Load config if not already loaded
        try:
            config = load_config()
            immich_cfg = get_immich_config(config)
        except Exception:
            immich_cfg = {}

        self.base_url = normalize_base_url(base_url or get_base_url())
        self.api_key = api_key or get_api_key()

        # Check config for ssl_verify setting
        if not verify_ssl:
            pass  # Explicit setting takes precedence
        elif immich_cfg.get("ssl_verify") is False:
            verify_ssl = False

        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")

        self.api_base = f"{self.base_url}/api"
        self._client = httpx.AsyncClient(
            headers={"x-api-key": self.api_key},
            timeout=httpx.Timeout(60.0, connect=30.0),
            verify=verify_ssl,
        )

    def _url(self, path: str) -> str:
        """Build full URL for an API path."""
        return f"{self.api_base}{path}"

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    async def get_albums(self) -> list[dict]:
        """Get list of albums."""
        resp = await self._client.get(self._url("/albums"))
        resp.raise_for_status()
        return resp.json()

    async def create_album(self, name: str) -> dict:
        """Create a new album."""
        resp = await self._client.post(
            self._url("/albums"),
            json={"albumName": name},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_or_create_album(self, name: str) -> dict:
        """Get existing album by name or create it."""
        albums = await self.get_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album
        return await self.create_album(name)

    async def upload_asset(
        self,
        file: Path | BinaryIO,
        filename: str | None = None,
        mime_type: str | None = None,
        file_timestamp: datetime | None = None,
    ) -> dict:
        """Upload an asset (image/video) to Immich.

        Returns the full server response, which is one of:
        - {"status": "created", "id": "<uuid>"} — new asset uploaded
        - {"status": "duplicate", "id": "<uuid>"} — same checksum already exists
        - {"status": "replaced", ...} — replaced an existing asset (requires header)

        NOTE: Immich's API does NOT accept ``originalFileName`` updates through
        the ``PATCH /api/assets/{id}`` endpoint (verified against
        ``UpdateAssetDto`` in server source — that DTO has no such field).
        To rename an asset you must delete and re-upload it.
        """
        opened_file: BinaryIO | None = None
        if isinstance(file, Path):
            filename = filename or file.name
            mime_type = mime_type or _guess_mime(file)
            stat = file.stat()
            mtime = stat.st_mtime
            opened_file = file.open("rb")
            file = opened_file
        else:
            # BinaryIO doesn't have stat info, use current time
            mtime = datetime.now(tz=timezone.utc).timestamp()

        device_id = "immich-skill"
        device_asset_id = str(uuid.uuid4())
        # ISO 8601 in UTC with explicit "Z" suffix. Immich's DTO requires
        # the timezone — a naive ``datetime.isoformat()`` (no offset)
        # produces HTTP 400 ``Validation failed`` on the fileCreatedAt
        # and fileModifiedAt fields.
        timestamp = file_timestamp or datetime.fromtimestamp(mtime, tz=timezone.utc)
        file_created_at = _format_datetime(timestamp)
        file_modified_at = file_created_at

        # Immich supports non-ASCII (Chinese, etc.) filenames in the
        # multipart ``filename`` field — they round-trip correctly in
        # both the upload response and the GET response. No sanitization.
        files = {
            "assetData": (filename, file, mime_type or "application/octet-stream"),
            "deviceAssetId": (None, device_asset_id),
            "deviceId": (None, device_id),
            "fileCreatedAt": (None, file_created_at),
            "fileModifiedAt": (None, file_modified_at),
        }
        try:
            resp = await self._client.post(self._url("/assets"), files=files)
        finally:
            if opened_file:
                opened_file.close()
        resp.raise_for_status()
        result = resp.json()
        # Server returns 200 with status="duplicate" or "replaced" for
        # known checksums; treat as a normal success.
        if "status" not in result and "id" in result:
            result["status"] = "created"
        return result

    async def get_asset(self, asset_id: str) -> dict:
        """Get an asset by ID."""
        resp = await self._client.get(self._url(f"/assets/{asset_id}"))
        resp.raise_for_status()
        return resp.json()

    async def wait_for_asset_metadata(
        self,
        asset_id: str,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
    ) -> dict:
        """Wait until Immich finishes extracting metadata for an asset."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            asset = await self.get_asset(asset_id)
            if asset.get("hasMetadata"):
                return asset
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"Immich metadata extraction timed out for asset {asset_id}"
                )
            await asyncio.sleep(poll_interval)

    async def update_asset(
        self,
        asset_id: str,
        *,
        date_time_original: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Update supported asset metadata fields."""
        payload = {}
        if date_time_original is not None:
            payload["dateTimeOriginal"] = date_time_original
        if description is not None:
            payload["description"] = description
        if not payload:
            raise ValueError("At least one asset field must be provided")
        resp = await self._client.patch(
            self._url(f"/assets/{asset_id}"),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def update_asset_description(self, asset_id: str, description: str) -> dict:
        """Update an asset's description via PATCH.

        Note: ``originalFileName`` is NOT supported here (see
        ``upload_asset`` docstring). The description is stored in
        ``asset_exif.description`` and is visible in the Immich web UI
        asset detail panel.
        """
        return await self.update_asset(asset_id, description=description)

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> dict:
        """Add assets to an album."""
        resp = await self._client.put(
            self._url(f"/albums/{album_id}/assets"),
            json={"ids": asset_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


def _guess_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    ext = path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".ts": "video/mp2t",
    }
    return mime_map.get(ext, "application/octet-stream")


def _format_datetime(value: datetime) -> str:
    """Format a datetime as an explicit UTC ISO 8601 timestamp."""
    if value.tzinfo is None:
        raise ValueError("file_timestamp must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
