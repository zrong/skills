# Immich API: verified pitfalls and how to diagnose them

Every claim here was checked against the actual server code/DTO in the
`ghcr.io/immich-app/immich-server` container (look in
`/usr/src/app/server/dist/controllers/` and `dist/dtos/`). When in
doubt, the same recipe (dump + grep the container) still works.

## 1. `originalFileName` cannot be changed via API

`PATCH /api/assets/{id}` (and `PUT`) silently ignore the field.
The `UpdateAssetDto` schema in
`/usr/src/app/server/dist/dtos/asset.dto.js` only accepts:

```
isFavorite, visibility, dateTimeOriginal, latitude, longitude,
rating, description, livePhotoVideoId
```

**Symptom:** PATCH returns 200, `updatedAt` advances, but the
filename is unchanged. There is no error — it's a silent no-op.

**How to verify yourself:**

```bash
docker exec immich_server cat \
  /usr/src/app/server/dist/dtos/asset.dto.js \
  | grep -B 1 -A 30 "UpdateAssetBaseSchema"
```

**Workaround:** Delete the asset and re-upload. Or write the
original name to `description` (PATCH field) and accept that the
visible filename stays as whatever was used on upload.

## 2. `fileCreatedAt` and `fileModifiedAt` require a timezone

The DTO uses Zod's strict ISO-8601 datetime validator (regex in
`/usr/src/app/server/dist/dtos/asset-media.dto.js`). Naive ISO
strings — no `Z`, no `+HH:MM` — fail with:

```json
{"message":"Validation failed","errors":[{
  "origin":"string","code":"invalid_format",
  "format":"datetime",
  "path":["fileCreatedAt"],
  "message":"Invalid input: expected ISO 8601 datetime string, received string"
}]}
```

**Symptom:** HTTP 400, but the message is buried in
`errors[].message`, not at the top level. If you only look at
`{"message":"Validation failed"}` you'll think it's a generic
error.

**Correct format:**

```python
from datetime import datetime, timezone
datetime.fromtimestamp(mtime, tz=timezone.utc) \
    .isoformat().replace("+00:00", "Z")
# → "2026-07-05T00:00:00Z"
```

**Wrong format (causes 400):**

```python
datetime.fromtimestamp(mtime).isoformat()
# → "2026-07-05T00:00:00.123456"   # no timezone on Linux
```

**Diagnostic recipe:** When you get HTTP 400 from
`/api/assets`, parse `errors[].path[]` to find which field failed.
Don't trust the top-level `message`.

For videos, valid upload fields are not enough to control the final timeline
date. Immich's asynchronous metadata extraction can read an embedded MP4
`creation_time` and replace `fileCreatedAt`. When upload time is required, wait
until `GET /api/assets/{id}` reports `hasMetadata=true`, then PATCH the asset's
`dateTimeOriginal` with the timezone-aware upload timestamp. The skill's
default `asset_time_source = "upload"` policy performs this sequence.

## 3. Non-ASCII filenames work — the earlier "400 on Chinese
filename" diagnosis was wrong

Multipart `filename` with Chinese / accented / emoji characters
round-trips correctly. Direct evidence: the live database has
rows like `生日视频.MOV`, `IMG_3129.mov` (mixed scripts). The
original `client.py` sanitize (`re.sub(r'[^\x00-\x7F]', '_', fn)`)
was a red herring added on a wrong diagnosis — it was masking
issue #2 above (missing timezone), and the resulting `test.mp4`
filenames in the library were the side effect.

**Don't re-add non-ASCII sanitization to the client.** The only
"real" 400 trap on the upload path is the timezone (issue #2).

## 4. `duplicate` / `replaced` are normal success responses

The server returns HTTP 200 with one of:

```json
{"status":"created",   "id":"<uuid>"}
{"status":"duplicate", "id":"<uuid>"}   // checksum already exists
{"status":"replaced",  ...}              // requires X-Immich-Replace header
```

The Python `httpx` client does NOT raise on these — they are 200.
But a previous version of the wrapper raised on the response
shape and lost the asset id. `client.upload_asset` now
normalizes: any response with an `id` and no `status` is treated
as `created`. A response with `status` passes through verbatim.

**Always check `result["status"]`** before assuming "new upload".

## 5. `description` is stored in `asset_exif`, not on `asset`

PATCH sets `asset_exif.description` (verified via direct SQL on
the `asset_exif` table). The PATCH response body and the GET
`/api/assets/{id}` response both omit the `description` key,
which is why a successful PATCH can look like it "did nothing"
in the API response — you have to query the asset in the web UI
or read `asset_exif` directly to confirm.

## 6. Generic debugging recipe for "Immich 400/422 with no obvious cause"

```bash
# 1. Get the exact validation error from the server
docker logs immich_server --tail 200 | grep -iE "validation|invalid|reject" | tail

# 2. The NestJS API doesn't always log validation errors.
#    Reproduce with curl and read the response body:
curl -sS -X POST "${BASE_URL}/api/assets" \
  -H "x-api-key: $KEY" \
  -F "assetData=@/path/to/file.mp4" \
  -F "deviceAssetId=hermes-$(date +%s)" \
  -F "deviceId=hermes-agent" \
  -F "fileCreatedAt=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \
  -F "fileModifiedAt=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
# ↑ reading the response body is the only reliable way to see
#   which Zod field failed.

# 3. Cross-check against the actual DTO schema in the container
docker exec immich_server grep -E "zod_1\.default\.(string|uuid|object)" \
  /usr/src/app/server/dist/dtos/asset-media.dto.js | head -30

# 4. Inspect the database directly if you need to know the truth
docker exec immich_postgres psql -U postgres -d immich \
  -c "SELECT id, \"originalFileName\", description \
      FROM asset a LEFT JOIN asset_exif e ON a.id = e.\"assetId\" \
      WHERE a.id = '<UUID>';"
```

**Core lesson:** When Immich returns 4xx with a vague
`{"message":"Validation failed"}`, the validation error is in
`response.errors[].path[]` and `response.errors[].message`. Pull the
actual server DTO from the container to confirm which fields
the validator enforces, and don't trust prior-session diagnoses
(this very file replaced one such wrong diagnosis in commit
`a99422b`).

## 7. v3 (v3.0.2) breaking changes — verified live 2026-10

Server upgraded v1.x → v3.0.2. What actually broke or changed:

- **Upload DTO dropped `deviceAssetId`/`deviceId`.** Sending them is
  ignored (not rejected), so old code keeps working, but they are dead
  weight — `client.upload_asset` no longer sends them. The curl recipe
  in section 6 above is historical; drop the two `-F device...` lines.
- **`GET /api/albums/{id}/assets` is gone (404).** List album contents
  via `POST /api/search/metadata` with `{"albumIds": [id]}`, paginating
  `page`/`size`.
- **Search pagination: `nextPage` is a STRING** (`"2"`). Passing it back
  as `page` fails with `Validation failed: expected number, received
  string` — `int()` it first. `page`/`size` must be JSON numbers.
- **`PUT /api/albums/{id}/assets` returns an array**, not the v1
  `{successfullyAdded, alreadyInAlbum}` object.
- **Album members shape:** GET returns `albumUsers:
  [{"user": {"id", "email", "name"}, "role": "owner"|"editor"|"viewer"}]`
  — the owner is INSIDE the list, but `PUT /api/albums/{id}/users`
  rejects an owner entry with `400 {"message":"Cannot add another
  owner"}`. Strip role=owner from the payload; existing members are
  preserved when you PUT the merged non-owner list.
- **`AlbumResponseDto.assets` removed** — the album list no longer
  embeds assets; `assetCount` is available instead.
- **Real EXIF lives in `exifInfo.dateTimeOriginal`.** The top-level
  `dateTimeOriginal` on `AssetResponseDto` is usually empty.
  `localDateTime` is the local-calendar rendering when real EXIF exists,
  but it is ALSO backfilled from `fileCreatedAt` when EXIF is missing —
  so it is not evidence of a real shooting date on its own.
- **Folder endpoints (new capability):** `GET /api/view/folder/unique-paths`
  lists asset directory paths; `GET /api/view/folder?path=` returns
  DIRECT children only (`LIKE path/%` and `NOT LIKE path/%/%`) — recurse
  yourself. Both filter to `visibility=timeline`, non-trashed,
  owner-scoped assets, and need the `folder.read` API-key permission.
- **Old `/api/view/folders` is gone** (route-level 404, with or without
  `assetId`); `/api/openapi.json` no longer serves the spec (returns
  the SPA HTML). Read the OpenAPI from
  `https://api.immich.app/openapi.json` or the GitHub source instead.
- **Granular API-key permissions matter now.** Working set for this
  skill: `asset.upload/read/update`, `album.read/create`,
  `albumAsset.create`, `albumUser.create`, `album.delete`,
  `library.read/update` (scan also requires an admin account),
  `user.read`, `folder.read`. Missing ones return
  `403 {"message":"Missing required permission: <name>"}`.

## 8. External-library semantics worth knowing

- Library scan is **library-wide and mtime-incremental**; there is no
  per-subfolder scan. After the scan job, faceDetection/OCR/smartSearch
  queues pick up new assets automatically.
- NAS mounts (NFS/SMB) have no working inotify — Immich's "watch"
  feature is useless there; scan on demand (`immich scan`) or schedule
  it server-side.
- When files move/merge on disk, a scan makes the old path's assets
  invisible (offline) and creates fresh asset records at the new path.
  The old records linger in the DB (harmless, invisible in timeline and
  folder view) — albums built from the old records drain to 0 assets
  and should be deleted; albums sync from the new path via
  `immich album-sync`.
- Date derivation for folder→album naming (implemented in
  `immich.albums.derive_album_name`): name parts are authoritative;
  missing bounds come from real EXIF clustered modal ±30 days (kills
  camera-clock strays and poster-frame generation dates); fileCreatedAt
  modal (≥50% agreement) is last-resort weak evidence and never extends
  a name-given start by more than 7 days (copy-date guard).
