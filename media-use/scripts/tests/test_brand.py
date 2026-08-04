import json
import inspect
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import skipUnless

from media_use.brand import (
    BrandMediaInfo,
    _BUNDLED_CJK_FONT,
    build_filter_graph,
    calculate_canvas_width,
    calculate_video_bitrate_kbps,
    main,
    parse_bitrate_kbps,
    parse_watermark_width,
    resolve_text_watermark_font,
)


class BrandHelpersTest(TestCase):
    def setUp(self) -> None:
        self.main = BrandMediaInfo(duration=279.6, width=1280, height=720, has_audio=True)
        self.outro = BrandMediaInfo(duration=4.25, width=1920, height=1080, has_audio=False)

    def test_parse_bitrate(self) -> None:
        self.assertEqual(parse_bitrate_kbps("64k"), 64)
        self.assertEqual(parse_bitrate_kbps("1.5M"), 1500)
        self.assertEqual(parse_bitrate_kbps("128"), 128)

    def test_target_bitrate_reserves_container_headroom(self) -> None:
        bitrate = calculate_video_bitrate_kbps(20, 283.85, 64)
        self.assertEqual(bitrate, 471)

    def test_canvas_and_relative_watermark_width(self) -> None:
        width = calculate_canvas_width(self.main, 480)
        self.assertEqual(width, 854)
        self.assertEqual(parse_watermark_width("15%", width), 128)
        self.assertEqual(parse_watermark_width("140", width), 140)

    def test_cli_default_watermark_width(self) -> None:
        parameters = inspect.signature(main).parameters
        self.assertEqual(parameters["watermark_width"].default.default, "35%")
        self.assertEqual(parameters["text_watermark_coverage"].default.default, 0.8)
        self.assertEqual(parameters["text_watermark_opacity"].default.default, 0.45)

    def test_explicit_text_watermark_font_is_preserved(self) -> None:
        font = Path("/tmp/cjk-font.ttf")
        self.assertEqual(resolve_text_watermark_font(font, "虎澈AI"), font)

    def test_cjk_text_uses_bundled_font(self) -> None:
        self.assertTrue(_BUNDLED_CJK_FONT.is_file())
        self.assertEqual(resolve_text_watermark_font(None, "虎澈AI"), _BUNDLED_CJK_FONT)

    def test_filter_graph_adds_watermark_outro_and_silent_audio(self) -> None:
        graph, video_label, audio_label = build_filter_graph(
            self.main,
            width=854,
            height=480,
            fps=20,
            outro_info=self.outro,
            watermark_input_index=2,
            watermark_width=140,
            watermark_scope="main",
        )
        self.assertIn("overlay=x=20:y=H-h-20", graph)
        self.assertIn("anullsrc=r=44100", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)
        self.assertEqual(video_label, "[joinedv]")
        self.assertEqual(audio_label, "[outa]")

    def test_filter_graph_can_watermark_entire_output(self) -> None:
        graph, video_label, _ = build_filter_graph(
            self.main,
            width=854,
            height=480,
            fps=30,
            outro_info=self.outro,
            watermark_input_index=2,
            watermark_scope="all",
            include_audio=False,
        )
        self.assertIn("[main][outro]concat=n=2:v=1:a=0[joinedv]", graph)
        self.assertIn("[joinedv][logo]overlay=", graph)
        self.assertEqual(video_label, "[outv]")

    def test_filter_graph_adds_optional_diagonal_text_watermark(self) -> None:
        graph, video_label, _ = build_filter_graph(
            self.main,
            width=854,
            height=480,
            fps=30,
            text_watermark="虎澈AI",
        )
        self.assertIn("drawtext=text='虎澈AI'", graph)
        self.assertIn("rotate=-0.512", graph)
        self.assertIn("[main][text_watermark]overlay=x=(W-w)/2:y=(H-h)/2", graph)
        self.assertEqual(video_label, "[main_text_marked]")


@skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg / ffprobe")
class BrandCliIntegrationTest(TestCase):
    def test_watermark_silent_outro_and_target_size(self) -> None:
        with tempfile.TemporaryDirectory(prefix="media-use-brand-test-") as temp_dir:
            root = Path(temp_dir)
            main = root / "main.mp4"
            outro = root / "outro.mp4"
            logo = root / "logo.png"
            output = root / "output.mp4"
            text_only_output = root / "text_only.mp4"

            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:sample_rate=44100",
                    "-t",
                    "0.6",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(main),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:size=320x180:rate=10",
                    "-t",
                    "0.4",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(outro),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:size=40x12",
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(logo),
                ],
                check=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "media_use.brand",
                    str(main),
                    "--watermark",
                    str(logo),
                    "--text-watermark",
                    "虎澈AI",
                    "--outro",
                    str(outro),
                    "--target-mb",
                    "0.2",
                    "--height",
                    "90",
                    "--fps",
                    "10",
                    "--audio-bitrate",
                    "32k",
                    "--preset",
                    "ultrafast",
                    "--non-interactive",
                    "-o",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "media_use.brand",
                    str(main),
                    "--text-watermark",
                    "Demo",
                    "--height",
                    "90",
                    "--fps",
                    "10",
                    "--video-bitrate",
                    "300k",
                    "--non-interactive",
                    "-o",
                    str(text_only_output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            probe_result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration,size:stream=codec_type,width,height,r_frame_rate",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(probe_result.stdout)
            video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")

            self.assertLess(int(data["format"]["size"]), 200_000)
            self.assertAlmostEqual(float(data["format"]["duration"]), 1.0, delta=0.1)
            self.assertEqual((video["width"], video["height"]), (160, 90))
            self.assertEqual(video["r_frame_rate"], "10/1")
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in data["streams"]))
            self.assertTrue(text_only_output.is_file())
