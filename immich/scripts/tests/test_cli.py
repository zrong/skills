import asyncio
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from immich import cli
from immich.metadata import VIDEO_METADATA_SCHEMA, metadata_sidecar_path


PUBLIC_RESULT = {
    "status": "created",
    "id": "asset-uuid",
    "public_url": "http://nas.example/s/inspiration/photos/asset-uuid",
}


class PublicUrlOutputTests(unittest.TestCase):
    def _patch_upload_dependencies(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        uploader = MagicMock()
        return (
            patch.object(cli, "load_config"),
            patch.object(cli, "get_default_album", return_value="Inspiration"),
            patch.object(cli, "ImmichClient", return_value=client),
            patch.object(cli, "ImmichUploader", return_value=uploader),
            uploader,
        )

    def _capture(self, coroutine):
        output = StringIO()
        with redirect_stdout(output):
            asyncio.run(coroutine)
        self.assertIn(f"Public URL: {PUBLIC_RESULT['public_url']}", output.getvalue())

    def test_local_upload_prints_public_url(self):
        load, album, client, uploader_cls, uploader = self._patch_upload_dependencies()
        uploader.upload_file = AsyncMock(return_value=dict(PUBLIC_RESULT))
        with load, album, client, uploader_cls:
            self._capture(
                cli.upload_files(
                    [Path("asset.jpg")],
                    None,
                    "原标题 #话题",
                    "source",
                )
            )
        uploader.upload_file.assert_awaited_once_with(
            Path("asset.jpg"),
            "Inspiration",
            description="原标题 #话题",
        )

    def test_explicit_metadata_file_is_forwarded(self):
        load, album, client, uploader_cls, uploader = self._patch_upload_dependencies()
        uploader.upload_file = AsyncMock(return_value=dict(PUBLIC_RESULT))
        metadata_file = Path("asset.metadata.json")
        with load, album, client, uploader_cls:
            self._capture(
                cli.upload_files(
                    [Path("asset.mp4")],
                    None,
                    metadata_file=metadata_file,
                )
            )
        uploader.upload_file.assert_awaited_once_with(
            Path("asset.mp4"),
            "Inspiration",
            description=None,
            metadata_path=metadata_file,
        )

    def test_batch_upload_prints_public_url(self):
        load, album, client, uploader_cls, uploader = self._patch_upload_dependencies()
        uploader.upload_files = AsyncMock(return_value=[dict(PUBLIC_RESULT)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "asset.jpg").touch()
            with load, album, client, uploader_cls:
                self._capture(
                    cli.batch_upload_files(path, ["jpg"], None, True, False)
                )

    def test_batch_delete_removes_adjacent_metadata(self):
        load, album, client, uploader_cls, uploader = self._patch_upload_dependencies()
        uploader.upload_files = AsyncMock(return_value=[dict(PUBLIC_RESULT)])
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            media_path = directory / "asset.mp4"
            media_path.touch()
            sidecar = metadata_sidecar_path(media_path)
            sidecar.write_text(
                '{"schema": "' + VIDEO_METADATA_SCHEMA + '"}',
                encoding="utf-8",
            )
            with load, album, client, uploader_cls:
                self._capture(
                    cli.batch_upload_files(
                        directory,
                        ["mp4"],
                        None,
                        False,
                        False,
                    )
                )
            self.assertFalse(media_path.exists())
            self.assertFalse(sidecar.exists())

    def test_upload_url_command_is_removed(self):
        error = StringIO()
        with patch.object(
            sys, "argv", ["immich", "upload-url", "https://example.com/video"]
        ), redirect_stderr(error):
            with self.assertRaisesRegex(SystemExit, "2"):
                cli.main()
        self.assertIn("invalid choice: 'upload-url'", error.getvalue())


if __name__ == "__main__":
    unittest.main()
