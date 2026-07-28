import json
import tempfile
import unittest
from pathlib import Path

from immich.metadata import (
    VIDEO_METADATA_SCHEMA,
    format_video_description,
    load_video_metadata,
    metadata_sidecar_path,
)


class VideoMetadataTests(unittest.TestCase):
    def test_formats_all_available_video_details(self):
        description = format_video_description(
            {
                "title": "原始标题",
                "description": "原始标题 #话题\n第二行",
                "author_name": "作者",
                "author_id": "author-id",
                "platform": "微信视频号",
                "published_at": "2024-07-03T16:00:00+08:00",
                "duration_seconds": 65,
                "media_id": "sph-id",
                "tags": ["话题", "#第二个", "话题"],
                "source_url": "https://weixin.qq.com/sph/sph-id",
            }
        )

        self.assertIn("标题：原始标题", description)
        self.assertIn("作者：作者", description)
        self.assertIn("作者 ID：author-id", description)
        self.assertIn("平台：微信视频号", description)
        self.assertIn("发布时间：2024-07-03T16:00:00+08:00", description)
        self.assertIn("时长：00:01:05", description)
        self.assertIn("视频 ID：sph-id", description)
        self.assertIn("原始描述：\n原始标题 #话题\n第二行", description)
        self.assertIn("话题：#话题 #第二个", description)
        self.assertIn("来源：https://weixin.qq.com/sph/sph-id", description)

    def test_omits_missing_fields_and_duplicate_description(self):
        self.assertEqual(
            format_video_description(
                {
                    "title": "标题",
                    "description": "标题",
                    "platform": "抖音",
                }
            ),
            "标题：标题\n平台：抖音",
        )

    def test_loads_adjacent_sidecar_and_validates_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "video.mp4"
            sidecar = metadata_sidecar_path(media_path)
            payload = {"schema": VIDEO_METADATA_SCHEMA, "title": "标题"}
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_video_metadata(media_path), payload)

            sidecar.write_text(json.dumps({"schema": "unknown"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported metadata schema"):
                load_video_metadata(media_path)


if __name__ == "__main__":
    unittest.main()
