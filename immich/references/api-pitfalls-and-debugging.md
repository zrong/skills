# Immich API：已验证的坑与诊断方法

本文每一条结论都对照过真实服务器代码/DTO（查看
`ghcr.io/immich-app/immich-server` 容器内的
`/usr/src/app/server/dist/controllers/` 与 `dist/dtos/`）。
拿不准时，同样的方法（dump 容器 + grep）依然可用。

## 1. `originalFileName` 无法通过 API 改名

`PATCH /api/assets/{id}`（以及 `PUT`）会静默忽略该字段。
`/usr/src/app/server/dist/dtos/asset.dto.js` 里的 `UpdateAssetDto`
只接受：

```
isFavorite, visibility, dateTimeOriginal, latitude, longitude,
rating, description, livePhotoVideoId
```

**症状：** PATCH 返回 200、`updatedAt` 前进，但文件名不变。没有报错——
是静默 no-op。

**自行验证：**

```bash
docker exec immich_server cat \
  /usr/src/app/server/dist/dtos/asset.dto.js \
  | grep -B 1 -A 30 "UpdateAssetBaseSchema"
```

**绕行方案：** 删除资产后重新上传；或者把原始文件名写进
`description`（PATCH 支持的字段），接受显示文件名保持上传时的样子。

## 2. `fileCreatedAt` 和 `fileModifiedAt` 必须带时区

DTO 使用 Zod 的严格 ISO-8601 校验（正则在
`/usr/src/app/server/dist/dtos/asset-media.dto.js`）。无时区的 ISO
字符串——既没有 `Z` 也没有 `+HH:MM`——会报：

```json
{"message":"Validation failed","errors":[{
  "origin":"string","code":"invalid_format",
  "format":"datetime",
  "path":["fileCreatedAt"],
  "message":"Invalid input: expected ISO 8601 datetime string, received string"
}]}
```

**症状：** HTTP 400，但关键信息埋在 `errors[].message`，不在顶层。
只看 `{"message":"Validation failed"}` 会误判成通用错误。

**正确格式：**

```python
from datetime import datetime, timezone
datetime.fromtimestamp(mtime, tz=timezone.utc) \
    .isoformat().replace("+00:00","Z")
# → "2026-07-05T00:00:00Z"
```

**错误格式（导致 400）：**

```python
datetime.fromtimestamp(mtime).isoformat()
# → "2026-07-05T00:00:00.123456"   # Linux 上无时区
```

**诊断套路：** `/api/assets` 返回 HTTP 400 时，解析
`errors[].path[]` 定位失败字段，不要相信顶层 `message`。

对视频来说，上传字段合法也不足以控制最终时间线日期。Immich 的异步
元数据提取可能读到 MP4 内嵌的 `creation_time` 并覆盖 `fileCreatedAt`。
需要用上传时间时，等 `GET /api/assets/{id}` 返回 `hasMetadata=true`
后，再 PATCH `dateTimeOriginal` 为带时区的上传时间戳。skill 默认的
`asset_time_source = "upload"` 策略就是这个流程。

## 3. 非 ASCII 文件名可用——此前"中文文件名导致 400"的诊断是错的

multipart `filename` 带中文/重音字符/emoji 都能正确往返。直接证据：
线上库里有 `生日视频.MOV`、`IMG_3129.mov`（中英混合）等记录。
最初 `client.py` 的 sanitize（`re.sub(r'[^\x00-\x7F]', '_', fn)`）
建立在错误诊断上——它掩盖的其实是上面 #2 的时区问题，库里出现
`test.mp4` 这类文件名正是那时的副作用。

**不要给客户端重新加非 ASCII sanitize。** 上传路径上真正会 400 的
只有时区（#2）。

## 4. `duplicate` / `replaced` 是正常成功响应

服务器返回 HTTP 200，body 为以下之一：

```json
{"status":"created",   "id":"<uuid>"}
{"status":"duplicate", "id":"<uuid>"}
{"status":"replaced",  ...}
```

`httpx` 对这些不会抛异常——它们是 200。但旧版包装层曾因响应形状
不符抛错并丢了 asset id。`client.upload_asset` 现已标准化：有 `id`
无 `status` 视为 `created`；带 `status` 的原样透传。

**判断"新上传"之前先检查 `result["status"]`。**

## 5. `description` 存在 `asset_exif`，不在 `asset` 上

PATCH 写的是 `asset_exif.description`（通过直查 `asset_exif` 表验证）。
PATCH 响应体和 `GET /api/assets/{id}` 响应都不含 `description` 键——
所以一次成功的 PATCH 在 API 响应里看起来"什么都没做"，要在网页端
查看或直查 `asset_exif` 才能确认。

## 6. "Immich 400/422 但看不出原因"的通用排查

```bash
# 1. 从服务器日志找确切的校验错误
docker logs immich_server --tail 200 | grep -iE "validation|invalid|reject" | tail

# 2. NestJS API 不一定记录校验错误。用 curl 复现并读响应体：
curl -sS -X POST "${BASE_URL}/api/assets" \
  -H "x-api-key: $KEY" \
  -F "assetData=@/path/to/file.mp4" \
  -F "fileCreatedAt=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \
  -F "fileModifiedAt=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
# ↑ 读响应体是看清哪个 Zod 字段失败的唯一可靠方式
# （v3 已移除 deviceAssetId/deviceId 字段，无需再传）

# 3. 与容器内实际 DTO 对账
docker exec immich_server grep -E "zod_1\.default\.(string|uuid|object)" \
  /usr/src/app/server/dist/dtos/asset-media.dto.js | head -30

# 4. 需要真相时直接查库
docker exec immich_postgres psql -U postgres -d immich \
  -c "SELECT id, \"originalFileName\", description \
      FROM asset a LEFT JOIN asset_exif e ON a.id = e.\"assetId\" \
      WHERE a.id = '<UUID>';"
```

**核心教训：** Immich 返回含糊的 `{"message":"Validation failed"}`
类 4xx 时，真正的校验错误在 `response.errors[].path[]` 和
`response.errors[].message`。从容器里拉实际 DTO 确认校验字段，
不要轻信历史会话的诊断结论（本文件自己就替换过一次错误诊断，
见 commit `a99422b`）。

## 7. v3（v3.0.2）破坏性变更——2026-10 实测

服务器从 v1.x 升到 v3.0.2 后，实际断掉/变化的部分：

- **上传 DTO 删除了 `deviceAssetId`/`deviceId`。** 发送它们会被忽略
  （不报错），老代码还能跑，但属于死代码——`client.upload_asset`
  已不再发送。第 6 节 curl 示例里的两行 `-F device...` 已过时，去掉。
- **`GET /api/albums/{id}/assets` 没了（404）。** 查相簿内容改用
  `POST /api/search/metadata` 带 `{"albumIds": [id]}`，用
  `page`/`size` 翻页。
- **搜索分页的 `nextPage` 是字符串**（`"2"`）。原样回传到 `page`
  会报 `Validation failed: expected number, received string`——
  先 `int()`。`page`/`size` 必须是 JSON 数字。
- **`PUT /api/albums/{id}/assets` 返回数组**，不再是 v1 的
  `{successfullyAdded, alreadyInAlbum}` 对象。
- **相簿成员结构：** GET 返回 `albumUsers:
  [{"user": {"id", "email", "name"}, "role": "owner"|"editor"|"viewer"}]`
  ——owner 也在列表里，但 `PUT /api/albums/{id}/users` 的 payload 带
  owner 条目会 `400 {"message":"Cannot add another owner"}`。提交前
  剔除 role=owner；PUT 合并后的非 owner 列表不会丢已有成员。
- **`AlbumResponseDto.assets` 移除**——相簿列表不再内嵌资产，
  改用 `assetCount`。
- **真实 EXIF 在 `exifInfo.dateTimeOriginal`。** `AssetResponseDto`
  顶层的 `dateTimeOriginal` 通常为空。`localDateTime` 在有真实 EXIF
  时是本地日历日渲染，但缺 EXIF 时会被 `fileCreatedAt` 回填——
  单独出现不能当作拍摄日期的证据。
- **文件夹接口（新能力）：** `GET /api/view/folder/unique-paths`
  列资产目录路径；`GET /api/view/folder?path=` 只返回直接子资产
  （`LIKE path/%` 且 `NOT LIKE path/%/%`）——递归要自己做。两者都
  只统计 `visibility=timeline`、未删除、当前用户可见的资产，且需要
  API key 的 `folder.read` 权限。
- **老的 `/api/view/folders` 已删除**（路由级 404，带不带 `assetId`
  都一样）；`/api/openapi.json` 不再返回规范（返回前端 SPA HTML）。
  要看 OpenAPI 用 `https://api.immich.app/openapi.json` 或 GitHub
  源码。
- **granular API key 权限开始真正生效。** 本 skill 的工作集：
  `asset.upload/read/update`、`album.read/create`、
  `albumAsset.create`、`albumUser.create`、`album.delete`、
  `library.read/update`（scan 还要求 admin 账号）、`user.read`、
  `folder.read`。缺失时返回
  `403 {"message":"Missing required permission: <name>"}`。

## 8. 外部图库语义备忘

- 图库扫描是**图库级、mtime 增量**；没有子目录级扫描。扫描任务
  完成后，faceDetection/OCR/smartSearch 队列会自动处理新资产。
- NAS 挂载（NFS/SMB）上 inotify 不可用——Immich 的 watch 功能
  形同虚设；要么按需扫描（`immich scan`），要么在服务端配置定时。
- 磁盘上移动/合并文件后，一次扫描会让旧路径资产转为不可见
  （offline），并在新路径生成全新的资产记录。旧记录滞留在库里
  （无害，时间线和文件夹视图都不显示）——基于旧记录建的相簿资产
  会清零，应当删除；相簿对齐新路径用 `immich album-sync`。
- 文件夹→相簿命名的日期推导（实现在
  `immich.albums.derive_album_name`）：名称给出的部分权威；缺失
  边界用真实 EXIF 的众数 ±30 天聚簇（过滤相机时钟离群值和海报帧
  生成日期）；`fileCreatedAt` 众数（≥50% 一致）是最后的弱证据，
  且不得把名称给出的起始日延后超过 7 天（拷贝日期保护）。

## 9. Tags API (v3) — verified 2026-10 on v3.0.2, re-verified after upgrade to v3.2.4

- **Bulk tag→asset assign is `PUT /api/tags/{id}/assets` with `{"ids": [...]}`** —
  the key is `ids`, NOT `assetIds` (400 `expected array, received undefined`
  otherwise). Remove with `DELETE /api/tags/{id}/assets` + same payload.
  Both are idempotent; the server dedupes tags per asset. A re-PUT of an
  already-tagged asset returns per-entry `{"success":false,"error":"duplicate"}`
  — harmless, but count it when verifying bulk results.
- **The per-entry `success:true` in the response does NOT mean it stuck.**
  See §10: external-library writes are rolled back after the sidecar
  write fails. Always verify by re-reading a sample asset (or
  `POST /api/search/metadata {"tagIds":[id]}`), never trust the PUT
  response alone.
- **Rename via `PUT /api/tags/{id}` can 500** with
  `update "tag" set  where "id" = $1` (empty SET clause — server bug when
  the payload has no editable fields). `tag.delete` permission is
  needed for `DELETE /api/tags/{id}`; without it a stray tag can only be
  removed in the web UI.
- **Thumbnails:** `GET /api/assets/{id}/thumbnail?size=` accepts only
  `original|fullsize|preview|thumbnail` — `small`/`medium` are v1 names
  and now 400. `thumbnail` ≈ 250px (cheap, good for classification),
  `preview` ≈ 1440px (needed to read screenshot text). Assets whose
  thumbnails were never generated (motion-photo video parts, some
  .tif/.cr2, broken records) 404 at every size — retry after
  thumbnailGeneration or tag from a sibling asset.
- **`POST /api/search/metadata` ignores `pageSize`** (always 250/page),
  and `total` is the page item count, not the match count. Paginate via
  the STRING `nextPage` until `null`. Default order is NOT `createdAt`
  — for "uploaded in year X" filter client-side on the DB-level
  `createdAt` (record insert time = upload/scan time; `fileCreatedAt` is
  the file's own mtime/ctime). `createdAfter`/`takenAfter` do not do
  what you'd hope here.

## 10. v3 writes .xmp sidecars on metadata change — read-only external library silently rolls back writes (verified live 2026-10 on v3.0.2; fix re-verified on v3.2.4)

**Symptom:** tags/descriptions set via API on external-library assets
return success (readable right after), then disappear within minutes.
Server log floods with
`MetadataRepository: Error writing exif data (...xmp): Error: Error creating file: /mnt/album/....xmp`
(3700+ in two hours on this instance). The `tag_asset` rows are gone in
the DB after the rollback (confirmed by direct psql query).

**Mechanism:** Immich v3 persists every tag/description change for
external-library assets by writing an Image::ExifTool/digiKam-style
`.xmp` sidecar next to the ORIGINAL file. When the library mount is
read-only for the container (`:ro` in compose — THE cause on this
instance, not file permissions), the sidecar write fails and the whole
metadata change is reverted; API `success:true` lies. Upload-library
assets (`/usr/src/app/upload/`) write sidecars fine and persist; each
metadata change leaves a `<file>.xmp` next to the original (2352
created during one tagging session), so a tagging campaign grows the
data dir by ~0.5 KB per touched asset.

**Fix applied on this instance (2026-10-02, verified):**
1. compose: drop `:ro` from the `/mnt/album` bind mount
2. compose: `user: "1000:1000"` on immich-server (image's `node` user
   == host uid 1000), so new sidecars are user-owned, not root-owned
3. BEFORE switching the user, chown all root-owned files in the data
   root to 1000:1000 (61860 entries here — originals were already 1000,
   sidecars/transcodes were root) — mixed ownership would re-trigger
   the rollback for old sidecars on the next metadata edit
4. Recreate the container, then verify: tag an external asset, wait
   >1 min, re-read; check the new sidecar's owner on the host

Bulk-tagging upload-library assets concurrently can still lose ~7% of
assignments (sidecar pipeline race under parallel write load); a second
idempotent apply pass fixes them — always finish with a full
expected-vs-server diff.

## 11. Partner sharing scope — "all photos" means the whole timeline, external libraries included (verified 2026-10)

- Partner sharing (`user-settings → Sharing → 好友/合作伙伴`) is
  **account-wide with no per-library/per-album scoping**: the partner sees
  every asset you own that is not archived, not trashed, and not
  hidden/locked — external-library assets included. Libraries are a
  storage concept, not a permission concept.
- **Archiving is the lever to opt assets out of partner visibility**:
  archived assets drop out of the partner's view (and your own timeline)
  but remain searchable/taggable for you. Trashing also excludes; locked
  folders exclude.
- Typical surprise: ID cards, business licences, medical reports and bank
  screenshots sitting in the upload library are visible to partners. A
  tagging pass that labels them (e.g. tag 证件资料/医疗健康) makes bulk
  archiving trivial: filter by tag → select → archive.
- **Face recognition does NOT cross users via partner sharing** — each
  user's people index covers only their own assets. Cross-user
  recognition requires the v3.2 **cluster group** (user-settings →
  Sharing → Cluster group): invitees must accept, then any group member
  clicks "Reset facial recognition", which wipes every group member's
  named people (names/birth dates) and re-runs recognition over the
  union of all members' faces. Names/birth dates are still per-user
  (not shared). Asset visibility is unchanged by cluster groups.
