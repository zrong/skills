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
curl -sS -X POST "http://nas.zengrong.net:2283/api/assets" \
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
`response.errors[].path[]` and `response.errors[].message`. Pull
the actual server DTO from the container to confirm which fields
the validator enforces, and don't trust prior-session diagnoses
(this very file replaced one such wrong diagnosis in commit
`a99422b`).
