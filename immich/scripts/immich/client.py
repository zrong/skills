"""Immich API client."""

import httpx
import uuid
from datetime import datetime
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
    ) -> dict:
        """Upload an asset (image/video) to Immich."""
        if isinstance(file, Path):
            filename = filename or file.name
            # Sanitize filename for Immich API (non-ASCII causes 400 errors)
            import re
            safe_name = re.sub(r'[^\x00-\x7F]', '_', filename)
            filename = safe_name
            mime_type = mime_type or _guess_mime(file)
            stat = file.stat()
            file_size = stat.st_size
            mtime = stat.st_mtime
            file = file.open("rb")
        else:
            # BinaryIO doesn't have stat info, use current time
            mtime = datetime.now().timestamp()
            file_size = 0

        device_id = "immich-skill"
        device_asset_id = str(uuid.uuid4())
        file_created_at = datetime.fromtimestamp(mtime).isoformat()
        file_modified_at = datetime.fromtimestamp(mtime).isoformat()

        files = {
            "assetData": (filename, file, mime_type or "application/octet-stream"),
            "deviceAssetId": (None, device_asset_id),
            "deviceId": (None, device_id),
            "fileCreatedAt": (None, file_created_at),
            "fileModifiedAt": (None, file_modified_at),
        }
        resp = await self._client.post(self._url("/assets"), files=files)
        resp.raise_for_status()
        return resp.json()

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
