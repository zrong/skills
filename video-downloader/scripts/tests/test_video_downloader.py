"""Tests for the video-downloader helper."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import video_downloader


class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._json({"code": 0, "data": {"version": "test"}})
            return
        if parsed.path == "/api/channels/parse_sph":
            share_url = (parse_qs(parsed.query).get("url") or [""])[0]
            if share_url.endswith("/auth-error"):
                self._json({"code": 400, "msg": "cloudflare.sphCookie not configured"})
                return
            if share_url.endswith("/no-video"):
                self._json({"code": 0, "data": {"data": {"feedInfo": {}}}})
                return
            media_path = "/media/not-video" if share_url.endswith("/bad-media") else "/media/video"
            self._json(
                {
                    "code": 0,
                    "data": {
                        "data": {
                            "authorInfo": {"nickname": "Test/Channel"},
                            "feedInfo": {
                                "description": "A test/video #topic\nsecond line",
                                "originVideoUrl": f"http://127.0.0.1:{self.server.server_port}{media_path}",
                            }
                        }
                    },
                }
            )
            return
        if parsed.path == "/media/video":
            body = b"test-video-content"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/media/not-video":
            body = b"not a video"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class VideoDownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_wx_channels_url_detection_and_backend_selection(self) -> None:
        sph_url = "https://weixin.qq.com/sph/A61BYtvDIe"
        preview_url = "https://channels.weixin.qq.com/finder-preview/pages/sph?id=A61BYtvDIe"
        self.assertTrue(video_downloader.is_wx_channels_url(sph_url))
        self.assertTrue(video_downloader.is_wx_channels_url(preview_url))
        self.assertFalse(video_downloader.is_wx_channels_url("https://weixin.qq.com/"))
        self.assertEqual(video_downloader.resolve_backend(sph_url, "auto", {}), "wx-channels")

    def test_yt_dlp_default_template_uses_author_directory(self) -> None:
        self.assertEqual(
            video_downloader.build_yt_dlp_output_template({}),
            "%(uploader,channel,creator,uploader_id,channel_id|unknown-author)s/"
            "%(title)s [%(id)s].%(ext)s",
        )

    def test_yt_dlp_configured_template_stays_below_author_directory(self) -> None:
        settings = {
            "yt_dlp_output_template": "clips/%(upload_date)s_%(title)s [%(id)s].%(ext)s"
        }
        self.assertEqual(
            video_downloader.build_yt_dlp_output_template(settings),
            "%(uploader,channel,creator,uploader_id,channel_id|unknown-author)s/"
            "clips/%(upload_date)s_%(title)s [%(id)s].%(ext)s",
        )

    def test_yt_dlp_template_rejects_paths_outside_author_directory(self) -> None:
        invalid_templates = (
            "",
            "/tmp/%(title)s.%(ext)s",
            "C:\\Downloads\\%(title)s.%(ext)s",
            "../%(title)s.%(ext)s",
        )
        for template in invalid_templates:
            with self.subTest(template=template), self.assertRaises(ValueError):
                video_downloader.build_yt_dlp_output_template(
                    {"yt_dlp_output_template": template}
                )

    def test_yt_dlp_command_keeps_root_and_template_separate(self) -> None:
        command = video_downloader.build_yt_dlp_download_command(
            Path("yt-dlp"),
            Path("D:/Downloads/video-downloads"),
            {"yt_dlp_output_template": "%(title)s [%(id)s].%(ext)s"},
            "https://example.com/video",
        )
        self.assertEqual(command[1:3], ["-P", "D:/Downloads/video-downloads"])
        self.assertEqual(command[3], "-o")
        self.assertTrue(command[4].endswith("/%(title)s [%(id)s].%(ext)s"))

    def test_api_url_normalization(self) -> None:
        self.assertEqual(
            video_downloader.normalize_wx_channels_api_url("http://127.0.0.1:2022/api/"),
            "http://127.0.0.1:2022",
        )
        with self.assertRaises(ValueError):
            video_downloader.normalize_wx_channels_api_url("file:///tmp/api")

    def test_cookie_format_uses_only_required_whitelist(self) -> None:
        cookies = [
            {"name": "hy_token", "value": "token-value"},
            {"name": "unrelated", "value": "must-not-leak"},
            {"name": "hy_source", "value": "web"},
            {"name": "hy_user", "value": "user-value"},
        ]
        self.assertEqual(
            video_downloader.format_wx_channels_cookie(cookies),
            "hy_source=web; hy_user=user-value; hy_token=token-value",
        )
        self.assertIsNone(
            video_downloader.format_wx_channels_cookie(
                [{"name": "hy_token", "value": "token-value"}]
            )
        )

    def test_cookie_config_update_preserves_other_values_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                "# keep this comment\napi:\n  port: 2022\ncloudflare:\n  sphCookie: old\n",
                encoding="utf-8",
            )
            video_downloader.update_wx_channels_cookie(
                config_path,
                "hy_user=user-value; hy_token=token-value",
            )
            content = config_path.read_text(encoding="utf-8")
            self.assertIn("# keep this comment", content)
            self.assertIn("port: 2022", content)
            self.assertIn("hy_user=user-value; hy_token=token-value", content)
            if video_downloader.os.name != "nt":
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_probe_and_download(self) -> None:
        reachable, version, error = video_downloader.probe_wx_channels_api(self.api_url)
        self.assertTrue(reachable)
        self.assertEqual(version, "test")
        self.assertEqual(error, "")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = video_downloader.download_wx_channels_video(
                "https://weixin.qq.com/sph/A61BYtvDIe",
                Path(temp_dir),
                self.api_url,
                2,
            )
            self.assertEqual(output.path.name, "A testvideo [A61BYtvDIe].mp4")
            self.assertEqual(output.path.parent.name, "Test_Channel")
            self.assertEqual(output.path.read_bytes(), b"test-video-content")
            self.assertEqual(
                output.description,
                "A test/video #topic\nsecond line",
            )

    def test_wx_channels_filename_preserves_chinese_and_removes_topics(self) -> None:
        description = (
            "疯狂的西瓜（2）#高能 #鬼畜 #新世纪咖啡战士 #重庆 #方言 #Ai视频"
        )
        self.assertEqual(
            video_downloader.safe_media_stem(description, "AOLM0zX09k"),
            "疯狂的西瓜（2） [AOLM0zX09k]",
        )

    def test_wx_channels_filename_title_is_limited_to_30_characters(self) -> None:
        stem = video_downloader.safe_media_stem("这是一个非常长的视频号标题" * 4, "video-id")
        title, suffix = stem.rsplit(" [", 1)
        self.assertEqual(len(title), 30)
        self.assertEqual(suffix, "video-id]")

    def test_wx_channels_filename_falls_back_when_only_topics_remain(self) -> None:
        self.assertEqual(
            video_downloader.safe_media_stem("#高能 #鬼畜", "video-id"),
            "视频号作品 [video-id]",
        )

    def test_legacy_root_download_is_moved_into_channel_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "A testvideo [A61BYtvDIe].mp4"
            legacy.write_bytes(b"legacy-video-content")
            output = video_downloader.download_wx_channels_video(
                "https://weixin.qq.com/sph/A61BYtvDIe",
                root,
                self.api_url,
                2,
            )
            self.assertEqual(output.path, root / "Test_Channel" / legacy.name)
            self.assertEqual(output.path.read_bytes(), b"legacy-video-content")
            self.assertFalse(legacy.exists())

    def test_parse_errors_are_explicit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "sphCookie not configured"):
            video_downloader.parse_wx_channels_video(
                "https://weixin.qq.com/sph/auth-error", self.api_url, 2
            )
        with self.assertRaisesRegex(RuntimeError, "playable video URL"):
            video_downloader.parse_wx_channels_video(
                "https://weixin.qq.com/sph/no-video", self.api_url, 2
            )

    def test_rejects_non_media_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "unexpected content type"):
                video_downloader.download_wx_channels_video(
                    "https://weixin.qq.com/sph/bad-media",
                    Path(temp_dir),
                    self.api_url,
                    2,
                )
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    @patch("video_downloader.refresh_wx_channels_cookie")
    @patch("video_downloader.download_wx_channels_video")
    def test_auth_error_refreshes_once_and_retries(self, download, refresh) -> None:
        expected = video_downloader.WxChannelsDownloadResult(
            Path("downloaded.mp4"), "description", "author"
        )
        download.side_effect = [RuntimeError("cloudflare.sphCookie not configured"), expected]
        result = video_downloader.download_wx_channels_with_auth_refresh(
            "https://weixin.qq.com/sph/A61BYtvDIe",
            Path("output"),
            self.api_url,
            2,
            {},
            Path("runtime"),
            False,
        )
        self.assertEqual(result, expected)
        self.assertEqual(download.call_count, 2)
        refresh.assert_called_once_with({}, Path("runtime"))

    def test_empty_video_result_is_treated_as_possible_expired_auth(self) -> None:
        self.assertTrue(
            video_downloader.is_wx_channels_auth_error(
                "wx_channels_download did not return a playable video URL"
            )
        )

    @patch("video_downloader.refresh_wx_channels_cookie")
    @patch("video_downloader.download_wx_channels_video")
    def test_non_interactive_auth_error_does_not_open_browser(self, download, refresh) -> None:
        download.side_effect = RuntimeError("cloudflare.sphCookie not configured")
        with self.assertRaisesRegex(RuntimeError, "--non-interactive"):
            video_downloader.download_wx_channels_with_auth_refresh(
                "https://weixin.qq.com/sph/A61BYtvDIe",
                Path("output"),
                self.api_url,
                2,
                {},
                Path("runtime"),
                True,
            )
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
