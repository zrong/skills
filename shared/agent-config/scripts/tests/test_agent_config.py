import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import agent_config  # noqa: E402


class AgentConfigTests(unittest.TestCase):
    def test_lookup_priority_and_global_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "repo" / "work"
            skill_dir = root / "skill"
            git_config = root / "repo" / agent_config.CONFIG_FILENAME
            global_config = root / "home" / ".agents" / agent_config.CONFIG_FILENAME
            cwd.mkdir(parents=True)
            skill_dir.mkdir()
            (root / "repo" / ".git").mkdir()
            global_config.parent.mkdir(parents=True)

            locations = [
                global_config,
                git_config,
                skill_dir / agent_config.CONFIG_FILENAME,
                cwd / agent_config.CONFIG_FILENAME,
            ]
            for expected in locations:
                expected.write_text(f"source = '{expected.name}'\n", encoding="utf-8")
                found = agent_config.find_config(
                    skill_dir,
                    cwd=cwd,
                    global_config=global_config,
                )
                self.assertEqual(found, expected.resolve())

    def test_candidates_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            candidates = agent_config.config_candidates(
                root,
                cwd=root,
                global_config=root / agent_config.CONFIG_FILENAME,
            )
            self.assertEqual(
                candidates,
                ((root / agent_config.CONFIG_FILENAME).resolve(),),
            )

    def test_explicit_relative_path_uses_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "custom.toml"
            config_path.write_text("enabled = true\n", encoding="utf-8")
            config, source = agent_config.load_config(
                root / "skill",
                path="custom.toml",
                cwd=root,
            )
            self.assertTrue(config["enabled"])
            self.assertEqual(source, config_path.resolve())

    def test_missing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = {
                "cwd": root / "work",
                "global_config": root / "global.toml",
            }
            config, source = agent_config.load_config(root / "skill", **kwargs)
            self.assertEqual((config, source), ({}, None))

            with self.assertRaises(agent_config.ConfigNotFoundError):
                agent_config.load_config(root / "skill", missing="raise", **kwargs)
            with self.assertRaises(agent_config.ConfigNotFoundError):
                agent_config.load_config(
                    root / "skill",
                    path="missing.toml",
                    **kwargs,
                )
            with self.assertRaisesRegex(ValueError, "missing"):
                agent_config.load_config(
                    root / "skill",
                    missing="invalid",
                    **kwargs,
                )

    def test_load_section_validates_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / agent_config.CONFIG_FILENAME
            config_path.write_text("[demo]\nenabled = true\n", encoding="utf-8")
            section, source = agent_config.load_section("demo", root, cwd=root)
            self.assertEqual(section, {"enabled": True})
            self.assertEqual(source, config_path.resolve())

            config_path.write_text("demo = 'invalid'\n", encoding="utf-8")
            with self.assertRaisesRegex(TypeError, r"\[demo\]"):
                agent_config.load_section("demo", root, cwd=root)


if __name__ == "__main__":
    unittest.main()
