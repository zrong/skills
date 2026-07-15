import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

from immich.client import ImmichClient


class ClientUrlTests(unittest.TestCase):
    def test_api_suffix_is_added_once(self):
        client = ImmichClient(
            base_url="https://immich.example/api",
            api_key="test-key",
        )
        try:
            self.assertEqual(client._url("/assets"), "https://immich.example/api/assets")
        finally:
            asyncio.run(client.close())


class UploadAssetStatusTests(unittest.TestCase):
    """Ensure upload_asset normalizes the various success responses Immich returns."""

    def _make_client(self):
        # Skip the real HTTPX client init dance; we only need _url().
        client = ImmichClient.__new__(ImmichClient)
        client.api_base = "https://immich.example/api"
        return client

    def _run(self, coro):
        return asyncio.run(coro)

    def test_duplicate_response_is_normalized(self):
        client = self._make_client()
        # Response has no "status" key but does have "id" — synthesize as "created".
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "abc-123"}
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = self._run(client.upload_asset(MagicMock(name="file", spec=[])))
        self.assertEqual(result, {"status": "created", "id": "abc-123"})

    def test_duplicate_with_status_passes_through(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "duplicate", "id": "abc-123"}
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = self._run(client.upload_asset(MagicMock(name="file", spec=[])))
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["id"], "abc-123")

    def test_replaced_status_passes_through(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "replaced", "id": "abc-123"}
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = self._run(client.upload_asset(MagicMock(name="file", spec=[])))
        self.assertEqual(result["status"], "replaced")

    def test_explicit_file_timestamp_is_sent_in_utc(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "created", "id": "abc-123"}
        mock_resp.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.post = AsyncMock(return_value=mock_resp)
        timestamp = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "中文标题.mp4"
            path.write_bytes(b"video")
            self._run(client.upload_asset(path, file_timestamp=timestamp))

        files = client._client.post.await_args.kwargs["files"]
        self.assertEqual(files["assetData"][0], "中文标题.mp4")
        self.assertEqual(files["fileCreatedAt"][1], "2026-07-15T08:30:00Z")
        self.assertEqual(files["fileModifiedAt"][1], "2026-07-15T08:30:00Z")


if __name__ == "__main__":
    unittest.main()
