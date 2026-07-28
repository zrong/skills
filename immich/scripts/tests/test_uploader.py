import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from immich.metadata import VIDEO_METADATA_SCHEMA, metadata_sidecar_path
from immich.uploader import ImmichUploader


class PublicUrlTests(unittest.TestCase):
    def test_remote_url_upload_api_is_removed(self):
        self.assertFalse(hasattr(ImmichUploader, "upload_url"))

    def _uploader(self, upload_result: dict):
        client = MagicMock()
        client.upload_asset = AsyncMock(return_value=dict(upload_result))
        client.wait_for_asset_metadata = AsyncMock(return_value={"hasMetadata": True})
        client.update_asset = AsyncMock(return_value={})
        client.get_or_create_album = AsyncMock(return_value={"id": "album-id"})
        client.add_assets_to_album = AsyncMock(return_value={})
        uploader = ImmichUploader(
            client,
            default_album="Inspiration",
            public_album_url="http://nas.example/s/inspiration/",
            asset_time_source="source",
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


class AssetMetadataTests(unittest.TestCase):
    def _client(self):
        client = MagicMock()
        client.upload_asset = AsyncMock(
            return_value={"status": "created", "id": "asset-uuid"}
        )
        client.wait_for_asset_metadata = AsyncMock(
            return_value={"hasMetadata": True}
        )
        client.update_asset = AsyncMock(return_value={})
        return client

    def test_upload_time_is_reapplied_after_metadata_extraction(self):
        client = self._client()
        uploader = ImmichUploader(client, asset_time_source="upload")

        asyncio.run(uploader.upload_file(Path("asset.mp4")))

        upload_time = client.upload_asset.await_args.kwargs["file_timestamp"]
        self.assertIsNotNone(upload_time.tzinfo)
        client.wait_for_asset_metadata.assert_awaited_once_with("asset-uuid")
        client.update_asset.assert_awaited_once_with(
            "asset-uuid",
            date_time_original=upload_time.isoformat(),
            description=None,
        )

    def test_source_time_does_not_override_extracted_metadata(self):
        client = self._client()
        uploader = ImmichUploader(client, asset_time_source="source")

        asyncio.run(uploader.upload_file(Path("asset.mp4")))

        client.upload_asset.assert_awaited_once_with(
            Path("asset.mp4"), file_timestamp=None
        )
        client.wait_for_asset_metadata.assert_not_awaited()
        client.update_asset.assert_not_awaited()

    def test_description_is_preserved_after_metadata_extraction(self):
        client = self._client()
        uploader = ImmichUploader(client, asset_time_source="source")
        description = "疯狂的西瓜（2）#高能 #鬼畜"

        asyncio.run(
            uploader.upload_file(Path("asset.mp4"), description=description)
        )

        client.wait_for_asset_metadata.assert_awaited_once_with("asset-uuid")
        client.update_asset.assert_awaited_once_with(
            "asset-uuid",
            date_time_original=None,
            description=description,
        )

    def test_adjacent_video_metadata_is_used_when_description_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "asset.mp4"
            path.write_bytes(b"video")
            metadata_sidecar_path(path).write_text(
                json.dumps(
                    {
                        "schema": VIDEO_METADATA_SCHEMA,
                        "title": "标题",
                        "description": "完整描述 #话题",
                        "author_name": "作者",
                        "platform": "抖音",
                        "media_id": "123",
                        "source_url": "https://www.douyin.com/video/123",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            client = self._client()
            uploader = ImmichUploader(client, asset_time_source="source")

            asyncio.run(uploader.upload_file(path))

            description = client.update_asset.await_args.kwargs["description"]
            self.assertIn("标题：标题", description)
            self.assertIn("作者：作者", description)
            self.assertIn("平台：抖音", description)
            self.assertIn("原始描述：\n完整描述 #话题", description)
            self.assertIn("来源：https://www.douyin.com/video/123", description)

    def test_explicit_description_overrides_adjacent_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "asset.mp4"
            metadata_sidecar_path(path).write_text("not json", encoding="utf-8")
            client = self._client()
            uploader = ImmichUploader(client, asset_time_source="source")

            asyncio.run(uploader.upload_file(path, description="显式描述"))

            client.update_asset.assert_awaited_once_with(
                "asset-uuid",
                date_time_original=None,
                description="显式描述",
            )


if __name__ == "__main__":
    unittest.main()
