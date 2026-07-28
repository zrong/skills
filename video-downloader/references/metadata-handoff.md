# Video Metadata Handoff

`video-downloader` 在每个成功下载的视频旁写入同名元数据侧车：

```text
video.mp4
video.mp4.metadata.json
```

侧车使用 `video-downloader.metadata/v1` schema，供 `immich` 等下游 skill
读取。字段均为可选，缺失信息不应猜测：

| 字段 | 含义 |
|------|------|
| `backend` | 下载 backend：`douyin`、`wx-channels` 或 `yt-dlp` |
| `platform` | 面向用户的平台名称 |
| `source_url` | 已清理凭据、签名参数和 fragment 的公开作品页 |
| `media_id` | 平台侧视频 ID |
| `title` | 未按文件名长度截断的视频标题 |
| `description` | 平台返回的完整原始文案 |
| `author_name` | 作者或频道名称 |
| `author_id` | 作者或频道 ID |
| `published_at` | 平台发布时间 |
| `duration_seconds` | 视频时长，单位为秒 |
| `tags` | 去除 `#` 前缀并去重的话题列表 |
| `file_name` | 本地媒体文件名，不包含绝对路径 |
| `downloaded_at` | 首次创建侧车的本地时间 |

示例：

```json
{
  "schema": "video-downloader.metadata/v1",
  "backend": "wx-channels",
  "platform": "微信视频号",
  "source_url": "https://weixin.qq.com/sph/example",
  "media_id": "example",
  "title": "视频标题",
  "description": "视频标题 #话题",
  "author_name": "视频号名称",
  "published_at": "2026-07-28T10:00:00+08:00",
  "tags": [
    "话题"
  ],
  "file_name": "视频标题 [example].mp4",
  "downloaded_at": "2026-07-28T12:00:00+08:00"
}
```

## 安全边界

- 不写入 Cookie、请求头、API key、浏览器 profile 或平台解析出的媒体直链。
- `yt-dlp` 只导出白名单字段，不保存完整 info JSON。
- `source_url` 只接受 HTTP(S) 作品页，并移除用户名、密码、fragment 及常见
  token、signature、key、expires 等查询参数。
- 侧车与媒体文件一起交给下游；下游成功删除媒体时也应删除对应侧车。
