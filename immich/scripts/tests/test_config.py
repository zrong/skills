import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from immich import config


class ConfigLookupTests(unittest.TestCase):
    def test_lookup_priority_and_global_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "repo" / "work"
            skill_dir = root / "skill"
            global_config = root / "home" / ".agents" / config.CONFIG_FILENAME
            cwd.mkdir(parents=True)
            skill_dir.mkdir()
            (root / "repo" / ".git").mkdir()
            global_config.parent.mkdir(parents=True)

            locations = [
                global_config,
                root / "repo" / config.CONFIG_FILENAME,
                skill_dir / config.CONFIG_FILENAME,
                cwd / config.CONFIG_FILENAME,
            ]

            previous_cwd = Path.cwd()
            os.chdir(cwd)
            try:
                with patch.object(config, "SKILL_DIR", skill_dir), patch.object(
                    config, "GLOBAL_CONFIG", global_config
                ):
                    for expected in locations:
                        expected.write_text("[immich]\n", encoding="utf-8")
                        found, _ = config._find_config()
                        self.assertEqual(found.resolve(), expected.resolve())
            finally:
                os.chdir(previous_cwd)

    def test_base_url_removes_legacy_api_suffix(self):
        self.assertEqual(
            config.get_base_url({"immich": {"base_url": "https://immich.example/api/"}}),
            "https://immich.example",
        )
        self.assertEqual(
            config.get_base_url({"immich": {"base_url": "https://immich.example"}}),
            "https://immich.example",
        )

    def test_public_album_url_removes_trailing_slash(self):
        self.assertEqual(
            config.get_public_album_url(
                {"immich": {"public_album_url": "https://immich.example/s/public/"}}
            ),
            "https://immich.example/s/public",
        )
        self.assertIsNone(config.get_public_album_url({"immich": {}}))


if __name__ == "__main__":
    unittest.main()
