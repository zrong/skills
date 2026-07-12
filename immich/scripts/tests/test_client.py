import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
