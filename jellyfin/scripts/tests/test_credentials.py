import importlib.util
import unittest
from pathlib import Path

import click

SCRIPT = Path(__file__).resolve().parents[1] / "jellyfin_tool.py"
spec = importlib.util.spec_from_file_location("jellyfin_tool_under_test", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class CredentialTests(unittest.TestCase):
    def test_server_and_omdb_keys_are_separate(self):
        cfg = {"api_key": "server-key", "base_url": "https://server.example.test/", "omdb": {"api_key": "omdb-key", "base_url": "https://omdb.example.test/"}}
        self.assertEqual(tool._get_server_config(cfg), ("https://server.example.test", "server-key"))
        self.assertEqual(tool._get_omdb_config(cfg), ("https://omdb.example.test", "omdb-key"))

    def test_old_omdb_field_requires_migration(self):
        with self.assertRaises(click.ClickException) as error:
            tool._get_omdb_config({"omdb_api_key": "never-print-this-value"})
        self.assertIn("[jellyfin.omdb].api_key", str(error.exception))
        self.assertNotIn("never-print-this-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
