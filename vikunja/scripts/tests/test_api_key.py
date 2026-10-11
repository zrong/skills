import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from click.testing import CliRunner

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))


class ApiKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent_config.toml").write_text(
                '[vikunja]\napi_key = "test-config-key"\n', encoding="utf-8"
            )
            cwd = Path.cwd()
            try:
                os.chdir(root)
                spec = importlib.util.spec_from_file_location("vikunja_tool_under_test", SCRIPT_DIR / "vikunja_tool.py")
                cls.tool = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cls.tool)
            finally:
                os.chdir(cwd)

    def test_direct_api_key_takes_precedence(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "env-key"}):
            actual = self.tool._resolve_api_key(
                {"api_key": "  direct-key  ", "api_key_env": "TEST_API_KEY"}, "vikunja"
            )
        self.assertEqual(actual, "  direct-key  ")

    def test_empty_direct_value_reads_environment(self):
        with patch.dict(os.environ, {"TEST_API_KEY": "env-key"}):
            self.assertEqual(
                self.tool._resolve_api_key({"api_key": "", "api_key_env": "TEST_API_KEY"}, "vikunja"),
                "env-key",
            )

    def test_missing_environment_is_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.tool._resolve_api_key({"api_key_env": "TEST_API_KEY"}, "vikunja"), "")

    def test_old_fields_require_migration_without_secret_output(self):
        for field in ("token", "token_env", "api_token", "api_token_env"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as error:
                    self.tool._resolve_api_key({field: "never-print-this-value"}, "vikunja")
                self.assertIn("api_key", str(error.exception))
                self.assertNotIn("never-print-this-value", str(error.exception))

    def test_noninteractive_help(self):
        result = CliRunner().invoke(self.tool.cli, ["--non-interactive", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)

    def test_wire_authentication_keeps_service_contract(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(self.tool.httpx, "Client", return_value=http_client):
            client = self.tool.VikunjaClient("https://service.example.test", "wire-key")
            self.assertEqual(client._request("GET", "resource"), {"ok": True})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer wire-key")

    def test_missing_key_stops_before_http(self):
        with patch.object(self.tool.httpx, "Client") as client_factory:
            client = self.tool.VikunjaClient("https://service.example.test", "")
            with self.assertRaises(SystemExit):
                client._request("GET", "resource")
            client_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
