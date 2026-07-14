---
name: immich
version: 26.28.34
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

在当前工作目录、skill 目录、Git 项目根目录或 `~/.agents/agent_config.toml` 中配置。查找优先级依次为：当前工作目录、skill 目录、Git 项目根目录、全局配置。

添加：

```toml
[immich]
base_url = "https://your-immich-server.com"
api_key = "your-api-key"
default_album = "My Photos"  # 可选
```

### 2. 上传本地文件

**运行方式（重要）：** 必须从 `agent_config.toml` 所在目录运行，且需要用
`--project` 指定 scripts 目录，再通过 `python -c` 调用 CLI：

```bash
# 从全局配置目录运行（推荐）
cd ~/.agents && uv run --project {SCRIPTS_DIR} python -c "from immich.cli import main; main()" upload /path/to/photo.jpg

# 指定 album
cd ~/.agents && uv run --project {SCRIPTS_DIR} python -c "from immich.cli import main; main()" upload /path/to/video.mp4 --album "Vacation"

# 批量上传
cd ~/.agents && uv run --project {SCRIPTS_DIR} python -c "from immich.cli import main; main()" upload /path/to/img1.jpg /path/to/img2.png --album "Trip"
```

`uv run immich upload ...`（文档中的简写形式）**不工作**——该包没有注册
console_scripts 入口点。使用上面的 python -c 调用方式。

### 2a. fallback：用 curl 直接上传

如果 Python 脚本上传失败（如遇到中文文件名的 400 错误），可以用 curl
作为 fallback，之后再调用 API 加入相册：

```bash
curl -s -X POST "${BASE_URL}/api/assets" \
  -H "x-api-key: ${API_KEY}" \
  -F "assetData=@/path/to/video.mp4;type=video/mp4" \
  -F "deviceAssetId=hermes-$(date +%s)" \
  -F "deviceId=hermes-agent" \
  -F "fileCreatedAt=2026-07-12T00:00:00.000Z" \
  -F "fileModifiedAt=2026-07-12T00:00:00.000Z"
```

加入默认相册（先查 album ID，再 PUT）：

```bash
# 查 album ID
ALBUM_ID=$(curl -s "${BASE_URL}/api/albums" -H "x-api-key: ${API_KEY}" | python3 -c "import sys,json;albums=json.load(sys.stdin);print(next(a['id'] for a in albums if a['albumName']=='ALBUM_NAME'))")

# 加入相册
curl -s -X PUT "${BASE_URL}/api/albums/${ALBUM_ID}/assets" \
  -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"ids\": [\"ASSET_ID\"]}"
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

### 6. 给已存在的 asset 补 description

Immich 没法改 `originalFileName`，但可以在 asset 详情面板的"Description"
字段里写任意文本（实际存储在 `asset_exif.description`）。如果之前的
上传因为 sanitize 把文件名改成 `test.mp4`，可以用这个子命令把原始
文件名、作者、来源 URL 写进去：

```bash
cd ~/.agents && uv run --project {SCRIPTS_DIR} python -c "from immich.cli import main; main()" \
  update-description <ASSET_UUID> "原文件名: xxx.mp4
抖音作者: 某某
抖音ID: 7659048818268179754
原始 URL: https://v.douyin.com/xxxxx/"
```

### 排障参考

- ghcr.io 镜像加速 & Immich v3 数据库迁移（pgvecto-rs → VectorChord）：
  `references/ghcr-mirroring-and-immich-migration.md`

## 配置说明

| 配置项 | 必需 | 说明 |
|--------|------|------|
| `base_url` | 是 | Immich 服务器地址，不要包含 `/api` 后缀；客户端会自动添加 |
| `api_key` | 是 | Immich API 密钥 |
| `default_album` | 否 | 默认上传的 Album 名称 |

## 已知陷阱

1. **`originalFileName` 不可通过 API 改名。** Immich 的 `UpdateAssetDto`
   字段（`isFavorite`、`visibility`、`dateTimeOriginal`、`latitude`、
   `longitude`、`rating`、`description`、`livePhotoVideoId`）里**没有**
   `originalFileName`。`PUT`/`PATCH /api/assets/{id}` 即使带这个字段
   也只更新 `updatedAt`，文件名不变。想改名必须**删除后重新上传**。

2. **`fileCreatedAt` / `fileModifiedAt` 必须带时区。** Immich 的 DTO
   校验 ISO 8601 datetime **必须带时区**（`Z` 或 `+08:00`）。
   `datetime.fromtimestamp(mtime).isoformat()` 在 Linux 上返回
   `2025-07-12T18:49:05.130080`（**无时区**），服务器返回
   `HTTP 400 {"message":"Validation failed", ...}`。
   `client.py::upload_asset` 现在用
   `datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00","Z")`
   生成 `2025-07-12T10:49:05.130080Z` 才合法。

3. **非 ASCII 文件名实际是支持的。** 之前 `client.py` 用
   `re.sub(r'[^\x00-\x7F]', '_', filename)` 把中文文件名替换成 ASCII
   下划线（`test.mp4`），但这个 sanitize 是**错误的**——Immich 服务器
   端能正确处理中文 multipart `filename` 字段，库里 `生日视频.MOV`、
   `IMG_3129.mov` 等中英文混合文件名都正常存储。已移除该 sanitize。
   真正的 400 原因是 #2 的时区，不是文件名。

4. **`base_url` 不要包含 `/api` 后缀。** 客户端会自动拼接 `/api/assets`
   等路径。如果配置中写了 `/api`，最终 URL 会变成 `/api/api/assets` → 404。

5. **必须使用 `default_album` 配置。** 如果用户在 `agent_config.toml`
   中设置了 `default_album`，上传时应使用该相册。Python 脚本通过
   `get_default_album()` 自动读取。curl fallback 方式需要手动查 album ID
   并调用加入相册 API。

6. **上传返回 `duplicate` 是正常成功。** 服务器对已存在 checksum 的
   文件返回 `HTTP 200 {"status":"duplicate","id":"<uuid>"}`。
   `client.py::upload_asset` 已把这种情况标准化成
   `{"status":"duplicate","id":"<uuid>"}` 返回值，不抛异常。

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
