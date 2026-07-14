import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from immich.uploader import ImmichUploader


class PublicUrlTests(unittest.TestCase):
    def test_remote_url_upload_api_is_removed(self):
        self.assertFalse(hasattr(ImmichUploader, "upload_url"))

    def _uploader(self, upload_result: dict):
        client = MagicMock()
        client.upload_asset = AsyncMock(return_value=dict(upload_result))
        client.get_or_create_album = AsyncMock(return_value={"id": "album-id"})
        client.add_assets_to_album = AsyncMock(return_value={})
        uploader = ImmichUploader(
            client,
            default_album="Inspiration",
            public_album_url="http://nas.example/s/inspiration/",
        )
        return uploader, client

    def test_created_and_duplicate_assets_get_public_url(self):
        for status in ("created", "duplicate"):
            with self.subTest(status=status):
                uploader, client = self._uploader(
                    {"status": status, "id": "asset-uuid"}
                )
                result = asyncio.run(
                    uploader.upload_file(Path("asset.jpg"), "Inspiration")
                )
                self.assertEqual(
                    result["public_url"],
                    "http://nas.example/s/inspiration/photos/asset-uuid",
                )
                client.add_assets_to_album.assert_awaited_once_with(
                    "album-id", ["asset-uuid"]
                )

    def test_other_album_and_missing_asset_id_have_no_public_url(self):
        uploader, _ = self._uploader({"status": "created", "id": "asset-uuid"})
        result = asyncio.run(uploader.upload_file(Path("asset.jpg"), "Private"))
        self.assertNotIn("public_url", result)

        uploader, client = self._uploader({"status": "created"})
        result = asyncio.run(
            uploader.upload_file(Path("asset.jpg"), "Inspiration")
        )
        self.assertNotIn("public_url", result)
        client.add_assets_to_album.assert_not_awaited()

    def test_album_add_failure_does_not_return_a_result(self):
        uploader, client = self._uploader({"status": "created", "id": "asset-uuid"})
        client.add_assets_to_album.side_effect = RuntimeError("album add failed")
        with self.assertRaisesRegex(RuntimeError, "album add failed"):
            asyncio.run(uploader.upload_file(Path("asset.jpg"), "Inspiration"))


if __name__ == "__main__":
    unittest.main()
