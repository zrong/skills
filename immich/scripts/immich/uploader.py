"""High-level uploader for local files."""

import asyncio
from datetime import datetime
from pathlib import Path

from immich.client import ImmichClient
from immich.config import (
    get_asset_time_source,
    get_default_album,
    get_public_album_url,
)


class ImmichUploader:
    """High-level uploader for local files and album assignment."""

    def __init__(
        self,
        client: ImmichClient,
        default_album: str | None = None,
        public_album_url: str | None = None,
        asset_time_source: str | None = None,
    ):
        self.client = client
        self.default_album = default_album or get_default_album()
        self.public_album_url = (
            public_album_url or get_public_album_url() or ""
        ).rstrip("/")
        self.asset_time_source = asset_time_source or get_asset_time_source()
        if self.asset_time_source not in {"upload", "source"}:
            raise ValueError("asset_time_source must be one of: source, upload")

    async def upload_file(
        self,
        path: Path,
        album_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Upload a local file to Immich, optionally to an album."""
        upload_time = None
        if self.asset_time_source == "upload":
            upload_time = datetime.now().astimezone()
        result = await self.client.upload_asset(path, file_timestamp=upload_time)
        asset_id = result.get("id")

        if asset_id and (upload_time or description is not None):
            await self.client.wait_for_asset_metadata(asset_id)
            await self.client.update_asset(
                asset_id,
                date_time_original=(
                    upload_time.isoformat() if upload_time else None
                ),
                description=description,
            )

        if album_name:
            album = await self.client.get_or_create_album(album_name)
            if asset_id:
                await self.client.add_assets_to_album(album["id"], [asset_id])
                if album_name == self.default_album and self.public_album_url:
                    result["public_url"] = (
                        f"{self.public_album_url}/photos/{asset_id}"
                    )

        return result

    async def upload_files(
        self,
        paths: list[Path],
        album_name: str | None = None,
    ) -> list[dict]:
        """Upload multiple files in parallel."""
        tasks = [self.upload_file(p, album_name) for p in paths]
        return await asyncio.gather(*tasks, return_exceptions=True)
