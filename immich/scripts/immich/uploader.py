"""Uploader for immich with remote URL download support via yt-dlp."""

import asyncio
import tempfile
from pathlib import Path

import yt_dlp

from immich.client import ImmichClient
from immich.config import get_default_album, get_public_album_url


class ImmichUploader:
    """High-level uploader with yt-dlp support for remote URLs."""

    def __init__(
        self,
        client: ImmichClient,
        default_album: str | None = None,
        public_album_url: str | None = None,
    ):
        self.client = client
        self.default_album = default_album or get_default_album()
        self.public_album_url = (
            public_album_url or get_public_album_url() or ""
        ).rstrip("/")

    async def upload_file(
        self,
        path: Path,
        album_name: str | None = None,
    ) -> dict:
        """Upload a local file to Immich, optionally to an album."""
        result = await self.client.upload_asset(path)

        if album_name:
            album = await self.client.get_or_create_album(album_name)
            asset_id = result.get("id")
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

    async def upload_url(
        self,
        url: str,
        album_name: str | None = None,
    ) -> dict:
        """Download a remote video/image and upload to Immich."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Download using yt-dlp
            ydl_opts = {
                "format": "best",
                "outtmpl": str(tmppath / "%(title)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
            }

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, self._download_yt_dlp, url, ydl_opts)

            # Find downloaded file
            files = list(tmppath.glob("*"))
            if not files:
                raise RuntimeError(f"yt-dlp failed to download: {url}")

            downloaded = files[0]
            return await self.upload_file(downloaded, album_name)

    def _download_yt_dlp(self, url: str, opts: dict):
        """Run yt-dlp download in executor."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
