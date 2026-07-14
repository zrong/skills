import asyncio
import unittest
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


if __name__ == "__main__":
    unittest.main()
