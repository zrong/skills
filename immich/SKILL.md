---
name: immich
description: 将图像和视频上传到 Immich 服务器。支持本地文件上传、远程URL下载上传、管理Album。当用户提到"上传到 Immich"、"上传图片"、"备份照片"、"上传视频"、"下载视频并上传"时使用此技能。
argument-hint: "[file-path 或 url] [--album album-name]"
allowed-tools: Bash(uv run *), Read, Glob, Edit
---

# Immich Skill

将图像和视频上传到 Immich 服务器。

## 路径约定

- `{SKILL_DIR}` = 本 skill 所在目录
- `{SCRIPTS_DIR}` = `{SKILL_DIR}/scripts/`

## 工作流程

### 1. 配置文件

在项目根目录或 skill 目录下创建 `agent_config.toml`，添加：

```toml
[immich]
base_url = "https://your-immich-server.com/api"
api_key = "your-api-key"
default_album = "My Photos"  # 可选
```

### 2. 上传本地文件

```bash
# 使用默认 album
uv run immich upload /path/to/photo.jpg

# 指定 album
uv run immich upload /path/to/video.mp4 --album "Vacation"

# 批量上传（并行）
uv run immich upload /path/to/img1.jpg /path/to/img2.png /path/to/video.mp4 --album "Trip"
```

### 3. 下载远程视频并上传

使用 `yt-dlp` 下载支持网站（YouTube、Twitter、Instagram、Bilibili 等）的视频：

```bash
uv run immich upload-url "https://youtube.com/watch?v=xxx" --album "YouTube"
uv run immich upload-url "https://twitter.com/user/status/xxx" --album "Twitter"
```

### 4. 批量上传

批量上传指定目录下的文件（默认从 `~/Downloads` 上传 mp4 文件）：

```bash
# 上传 ~/Downloads 下所有 mp4 文件
uv run immich batch-upload

# 上传指定目录下的所有 mp4 和 jpg 文件
uv run immich batch-upload /path/to/photos jpg mp4

# 递归上传所有视频文件（包括子目录）
uv run immich batch-upload /path/to/videos mp4 mkv mov --recursive --album "Videos"

# 上传后不删除本地文件
uv run immich batch-upload --no-delete
```

### 5. 初始化和测试

```bash
uv run immich init
```

## 配置说明

| 配置项 | 必需 | 说明 |
|--------|------|------|
| `base_url` | 是 | Immich 服务器地址，需包含 `/api` 后缀 |
| `api_key` | 是 | Immich API 密钥 |
| `default_album` | 否 | 默认上传的 Album 名称 |

## Python API

```python
from immich.config import load_config, get_immich_config
from immich.client import ImmichClient
from immich.uploader import ImmichUploader

# 加载配置
load_config()

# 使用客户端
async with ImmichClient() as client:
    uploader = ImmichUploader(client)

    # 上传本地文件
    await uploader.upload_file(Path("photo.jpg"), album_name="My Album")

    # 上传多个文件（并行）
    await uploader.upload_files([Path("a.jpg"), Path("b.png")], album_name="Photos")

    # 下载 URL 并上传
    await uploader.upload_url("https://example.com/video.mp4", album_name="Downloads")
```
