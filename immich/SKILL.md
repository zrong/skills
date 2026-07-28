---
name: immich
version: 26.29.38
description: 将本地图像和视频上传到 Immich 服务器，支持批量上传、管理 Album 和公开链接。网络资源下载由 video-downloader 负责；当用户提到"上传到 Immich"、"上传图片"、"备份照片"、"上传视频"、"下载视频并上传 Immich"时使用此技能。
argument-hint: "[file-path] [--album album-name]"
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
public_album_url = "https://your-immich-server.com/s/shared-album-key"  # 可选
asset_time_source = "upload"  # 可选，upload（默认）或 source
```

### 2. 上传本地文件

使用 `--project` 指定 scripts 目录即可从任意工作目录调用 CLI。配置仍按前述
优先级查找，最终兜底为 `~/.agents/agent_config.toml`：

```bash
# 上传单个文件
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_PHOTO}"

# 指定 album
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_VIDEO}" --album "Vacation"

# 单文件上传并保留网络来源的完整原始描述
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_VIDEO}" --description "原标题 #话题1 #话题2"

# 显式指定 video-downloader 元数据侧车（通常无需指定，会自动查找相邻文件）
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_VIDEO}" --metadata-file "{METADATA_FILE}"

# 本次上传改用媒体拍摄/创建时间
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_PHOTO}" --asset-time source

# 批量上传
uv run --project {SCRIPTS_DIR} immich upload "{LOCAL_IMAGE_1}" "{LOCAL_IMAGE_2}" --album "Trip"
```

### 2a. fallback：用 curl 直接上传

如果 Python 脚本上传失败（如遇到时区缺失的 400 错误，见陷阱 #2），
可以用 curl 作为 fallback，之后再调用 API 加入相册：

```bash
UPLOAD_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
curl -s -X POST "${BASE_URL}/api/assets" \
  -H "x-api-key: ${API_KEY}" \
  -F "assetData=@/path/to/video.mp4;type=video/mp4" \
  -F "deviceAssetId=hermes-$(date +%s)" \
  -F "deviceId=hermes-agent" \
  -F "fileCreatedAt=${UPLOAD_AT}" \
  -F "fileModifiedAt=${UPLOAD_AT}"
```

视频可能包含旧的 `creation_time`，Immich 后台提取元数据后会覆盖上述时间；
curl fallback 必须等
`GET /api/assets/{id}` 返回 `hasMetadata=true`，再执行：

```bash
curl -s -X PATCH "${BASE_URL}/api/assets/${ASSET_ID}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"dateTimeOriginal\": \"${UPLOAD_AT}\"}"
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

# 成功加入公开相册后，向用户展示资源链接
echo "Public URL: ${PUBLIC_ALBUM_URL%/}/photos/${ASSET_ID}"
```

### 3. 网络资源下载后上传

本 skill 不下载网络资源。用户提供视频 URL 并要求上传 Immich 时，按以下顺序组合两个 skill：

1. 使用 `video-downloader` 检查 backend 并完成下载。
2. 从下载结果中取得准确的本地媒体文件路径，并保留相邻的
   `<媒体文件名>.metadata.json`。
3. 将媒体路径传给本 skill 的 `upload` 命令。未显式提供
   `--description` 时，Immich 自动读取侧车，并把可获得的标题、作者、平台、
   发布时间、时长、视频 ID、原始文案、话题和来源页写入 Description。
4. 上传到默认公开相册后，将 `public_url` 返回给用户。

下载文件默认保留。只有用户明确要求清理时，才在确认 Immich 上传成功后删除。
用户直接提供本地文件或附件时，跳过 `video-downloader`，直接上传。

侧车使用 `video-downloader.metadata/v1` schema，字段契约见
video-downloader skill 的 `references/metadata-handoff.md`。显式
`--description` 优先于自动侧车；`--metadata-file` 可为单文件上传指定非相邻
侧车。侧车不存在时保持原有本地上传行为，不猜测视频信息。

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

批量上传会为每个媒体文件分别查找相邻侧车。启用默认删除行为时，上传成功的
媒体及其侧车会一起删除。

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
uv run --project {SCRIPTS_DIR} immich update-description <ASSET_UUID> "原文件名: xxx.mp4
抖音作者: 某某
抖音ID: 7659048818268179754
原始 URL: https://v.douyin.com/xxxxx/"
```

### 排障参考

- ghcr.io 镜像加速 & Immich v3 数据库迁移（pgvecto-rs → VectorChord）：
  `references/ghcr-mirroring-and-immich-migration.md`
- Immich API 已验证的坑（`originalFileName` 不可改、时区必带、中文
  文件名实际支持、`duplicate`/`replaced` 状态码、`description` 存在
  `asset_exif` 而非 `asset`，以及一个通用的 4xx 排障脚本）：
  `references/api-pitfalls-and-debugging.md`

## 配置说明

| 配置项 | 必需 | 说明 |
|--------|------|------|
| `base_url` | 是 | Immich 服务器地址，不要包含 `/api` 后缀；客户端会自动添加 |
| `api_key` | 是 | Immich API 密钥 |
| `default_album` | 否 | 默认上传的 Album 名称 |
| `public_album_url` | 否 | 默认相册的公开分享地址；成功加入该相册后生成资源公开链接 |
| `asset_time_source` | 否 | 时间线时间来源：`upload`（默认，本次上传时间）或 `source`（媒体/文件原始时间） |

## 已知陷阱

1. **`originalFileName` 不可通过 API 改名。** Immich 的 `UpdateAssetDto`
   字段（`isFavorite`、`visibility`、`dateTimeOriginal`、`latitude`、
   `longitude`、`rating`、`description`、`livePhotoVideoId`）里**没有**
   `originalFileName`。`PUT`/`PATCH /api/assets/{id}` 即使带这个字段
   也只更新 `updatedAt`，文件名不变。想改名必须**删除后重新上传**。

2. **`fileCreatedAt` / `fileModifiedAt` 必须带时区，且媒体元数据可能覆盖它们。** Immich 的 DTO
   校验 ISO 8601 datetime **必须带时区**（`Z` 或 `+08:00`）。
   `datetime.fromtimestamp(mtime).isoformat()` 在 Linux 上返回
   `2025-07-12T18:49:05.130080`（**无时区**），服务器返回
   `HTTP 400 {"message":"Validation failed", ...}`。
   `client.py::upload_asset` 现在用
   `datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00","Z")`
   生成 `2025-07-12T10:49:05.130080Z` 才合法。默认 `upload` 策略还会等待
   `hasMetadata=true` 后使用运行机器的本地时区偏移 PATCH `dateTimeOriginal`，
   避免 MP4 内嵌发布时间覆盖上传时间或造成时间线分组偏移。

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

7. **公开链接只属于默认相册。** 配置 `public_album_url` 后，资源成功加入
   `default_album` 才会返回 `public_url`，格式为
   `{public_album_url}/photos/{asset_id}`。上传到其他相册或加入相册失败时
   不应展示公开链接；`duplicate` 资源成功加入默认相册后仍应展示。

8. **默认时间策略是本次上传时间。** `asset_time_source = "upload"` 对新资源和
   `duplicate` 都按本次命令开始上传的时间更新 Immich 时间线。需要保留照片拍摄时间
   或视频内嵌创建时间时，配置 `source` 或单次使用 `--asset-time source`。

9. **下载视频的详细描述来自相邻侧车。** `video-downloader` 会生成
   `<媒体文件名>.metadata.json`。Immich 在没有显式 `--description` 时自动
   读取并格式化；缺失字段会省略，侧车不存在时不改变普通本地上传行为。

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
    result = await uploader.upload_file(
        Path("photo.jpg"),
        album_name="My Photos",
    )
    print(result.get("public_url"))

    # 上传多个文件（并行）
    await uploader.upload_files([Path("a.jpg"), Path("b.png")], album_name="Photos")

```
