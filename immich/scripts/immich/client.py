"""Immich API 客户端。"""

import asyncio
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from immich.config import get_api_key, get_base_url, normalize_base_url


class ImmichClient:
    """Immich API 客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool = True,
    ):
        from immich.config import get_immich_config, load_config

        # 未加载过配置则先加载
        try:
            config = load_config()
            immich_cfg = get_immich_config(config)
        except Exception:
            immich_cfg = {}

        self.base_url = normalize_base_url(base_url or get_base_url())
        self.api_key = api_key or get_api_key()

        # 读取配置里的 ssl_verify 开关
        if not verify_ssl:
            pass  # 显式传入优先
        elif immich_cfg.get("ssl_verify") is False:
            verify_ssl = False

        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.api_key:
            raise ValueError("api_key is required")

        self.api_base = f"{self.base_url}/api"
        self._client = httpx.AsyncClient(
            headers={"x-api-key": self.api_key},
            timeout=httpx.Timeout(60.0, connect=30.0),
            verify=verify_ssl,
        )

    def _url(self, path: str) -> str:
        """拼接 API 路径的完整 URL。"""
        return f"{self.api_base}{path}"

    async def close(self):
        """关闭 HTTP 客户端。"""
        await self._client.aclose()

    async def get_albums(self) -> list[dict]:
        """获取相簿列表。"""
        resp = await self._client.get(self._url("/albums"))
        resp.raise_for_status()
        return resp.json()

    async def create_album(self, name: str, description: str | None = None) -> dict:
        """创建新相簿。"""
        payload = {"albumName": name}
        if description:
            payload["description"] = description
        resp = await self._client.post(
            self._url("/albums"),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_or_create_album(self, name: str) -> dict:
        """按名称取现有相簿，不存在则创建。"""
        albums = await self.get_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album
        return await self.create_album(name)

    async def upload_asset(
        self,
        file: Path | BinaryIO,
        filename: str | None = None,
        mime_type: str | None = None,
        file_timestamp: datetime | None = None,
    ) -> dict:
        """上传一个资产（图片/视频）到 Immich。

        返回服务器完整响应，可能是：
        - {"status": "created", "id": "<uuid>"} — 新资产
        - {"status": "duplicate", "id": "<uuid>"} — 同 checksum 已存在
        - {"status": "replaced", ...} — 替换了现有资产（需要请求头）

        注意：Immich API 不接受通过 ``PATCH /api/assets/{id}`` 修改
        ``originalFileName``（已对照服务器源码验证，UpdateAssetDto 无此
        字段）。要改名必须删除后重新上传。
        """
        opened_file: BinaryIO | None = None
        if isinstance(file, Path):
            filename = filename or file.name
            mime_type = mime_type or _guess_mime(file)
            stat = file.stat()
            mtime = stat.st_mtime
            opened_file = file.open("rb")
            file = opened_file
        else:
            # BinaryIO 没有 stat 信息，用当前时间
            mtime = datetime.now(tz=timezone.utc).timestamp()

        # ISO 8601 UTC 且显式带 "Z" 后缀。Immich 的 DTO 要求时区——
        # 无时区的 ``datetime.isoformat()`` 会让 fileCreatedAt /
        # fileModifiedAt 报 HTTP 400 ``Validation failed``。
        timestamp = file_timestamp or datetime.fromtimestamp(mtime, tz=timezone.utc)
        file_created_at = _format_datetime(timestamp)
        file_modified_at = file_created_at

        # Immich 支持 multipart ``filename`` 里的非 ASCII（中文等）文件名，
        # 上传响应和 GET 响应都能正确往返，无需 sanitize。
        # v1 风格的 ``deviceAssetId``/``deviceId`` 表单字段已在 Immich v3
        # 从 AssetMediaCreateDto 删除；发送属于死代码（服务器忽略未知字段，
        # 但更严格的未来版本可能拒绝）。
        files = {
            "assetData": (filename, file, mime_type or "application/octet-stream"),
            "fileCreatedAt": (None, file_created_at),
            "fileModifiedAt": (None, file_modified_at),
        }
        try:
            resp = await self._client.post(self._url("/assets"), files=files)
        finally:
            if opened_file:
                opened_file.close()
        resp.raise_for_status()
        result = resp.json()
        # 已知 checksum 时服务器返回 200 + status="duplicate"/"replaced"，
        # 按正常成功处理。
        if "status" not in result and "id" in result:
            result["status"] = "created"
        return result

    async def get_asset(self, asset_id: str) -> dict:
        """按 ID 获取资产。"""
        resp = await self._client.get(self._url(f"/assets/{asset_id}"))
        resp.raise_for_status()
        return resp.json()

    async def wait_for_asset_metadata(
        self,
        asset_id: str,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
    ) -> dict:
        """等待 Immich 完成该资产的元数据提取。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            asset = await self.get_asset(asset_id)
            if asset.get("hasMetadata"):
                return asset
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"Immich metadata extraction timed out for asset {asset_id}"
                )
            await asyncio.sleep(poll_interval)

    async def update_asset(
        self,
        asset_id: str,
        *,
        date_time_original: str | None = None,
        description: str | None = None,
    ) -> dict:
        """更新资产支持的元数据字段。"""
        payload = {}
        if date_time_original is not None:
            payload["dateTimeOriginal"] = date_time_original
        if description is not None:
            payload["description"] = description
        if not payload:
            raise ValueError("At least one asset field must be provided")
        resp = await self._client.patch(
            self._url(f"/assets/{asset_id}"),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def update_asset_description(self, asset_id: str, description: str) -> dict:
        """通过 PATCH 更新资产描述。

        注意此处不支持 ``originalFileName``（见 ``upload_asset`` 的
        docstring）。description 存储在 ``asset_exif.description``，
        显示在 Immich 网页端资产详情面板。
        """
        return await self.update_asset(asset_id, description=description)

    # --- Tags ---

    async def list_tags(self) -> list[dict]:
        """列出全部 tag。"""
        resp = await self._client.get(self._url("/tags"))
        resp.raise_for_status()
        return resp.json()

    async def create_tag(self, name: str, color: str | None = None) -> dict:
        """创建 tag；已存在同名时返回既有定义。"""
        payload: dict = {"name": name}
        if color:
            payload["color"] = color
        resp = await self._client.post(self._url("/tags"), json=payload)
        resp.raise_for_status()
        return resp.json()

    async def assign_assets_to_tag(self, tag_id: str, asset_ids: list[str]) -> list[dict]:
        """批量把资产加入 tag。

        v3 的 payload 键是 ``ids``（不是 ``assetIds``）；返回逐资产
        ``{"id", "success"}`` 数组——注意 ``success:true`` 不保证持久，
        外部库资产受 sidecar 回滚影响（见 pitfalls §10），需回读抽查。
        """
        resp = await self._client.put(
            self._url(f"/tags/{tag_id}/assets"),
            json={"ids": asset_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def unassign_assets_from_tag(self, tag_id: str, asset_ids: list[str]) -> list[dict]:
        """把资产移出 tag（幂等）。"""
        resp = await self._client.request(
            "DELETE",
            self._url(f"/tags/{tag_id}/assets"),
            json={"ids": asset_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_thumbnail(self, asset_id: str, size: str = "thumbnail") -> bytes:
        """下载缩略图字节；size 仅接受 original|fullsize|preview|thumbnail。"""
        resp = await self._client.get(
            self._url(f"/assets/{asset_id}/thumbnail"),
            params={"size": size},
        )
        resp.raise_for_status()
        return resp.content

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> dict:
        """把资产加入相簿。

        v3 返回逐资产数组（不再是 v1 的 ``successfullyAdded`` 对象）。
        """
        resp = await self._client.put(
            self._url(f"/albums/{album_id}/assets"),
            json={"ids": asset_ids},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_album(self, album_id: str) -> None:
        """删除相簿。不会删除资产本身。"""
        resp = await self._client.delete(self._url(f"/albums/{album_id}"))
        resp.raise_for_status()

    # ---- 图库 / 文件夹 / 共享管理（v3，2026-10 新增）----

    async def list_libraries(self) -> list[dict]:
        """列出图库。需要 ``library.read``。"""
        resp = await self._client.get(self._url("/libraries"))
        resp.raise_for_status()
        return resp.json()

    async def find_library_by_import_path(self, import_path: str) -> dict | None:
        """按导入路径查找外部图库。"""
        for lib in await self.list_libraries():
            for p in lib.get("importPaths", []):
                if p == import_path.rstrip("/") or p.rstrip("/") in import_path or import_path.rstrip("/") in p:
                    return lib
        return None

    async def scan_library(self, library_id: str) -> None:
        """触发外部图库扫描（mtime 增量、图库级）。

        任务入队即返回（HTTP 204）。需要 ``library.update``。
        NAS 挂载没有 inotify，扫描是外部图库变更被感知的唯一途径。
        """
        resp = await self._client.post(self._url(f"/libraries/{library_id}/scan"))
        resp.raise_for_status()

    async def job_counts(self) -> dict[str, dict]:
        """任务队列名 -> jobCounts（active/waiting/failed）。需要队列读权限。"""
        resp = await self._client.get(self._url("/jobs"))
        resp.raise_for_status()
        jobs = resp.json()
        if isinstance(jobs, dict):
            return {name: (j.get("jobCounts", j) if isinstance(j, dict) else {}) for name, j in jobs.items()}
        return {j.get("id", str(i)): j.get("jobCounts", {}) for i, j in enumerate(jobs)}

    async def wait_for_library_scan(self, timeout_seconds: float = 600.0, poll_interval: float = 4.0) -> bool:
        """等待图库扫描队列空闲。空闲返回 True，超时返回 False。"""
        import asyncio as _asyncio

        loop = _asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            counts = await self.job_counts()
            lib = next((v for k, v in counts.items() if "librar" in k.lower()), {})
            if not lib.get("active") and not lib.get("waiting"):
                return True
            if loop.time() >= deadline:
                return False
            await _asyncio.sleep(poll_interval)

    async def unique_folder_paths(self) -> list[str]:
        """列出资产的去重文件夹路径。需要 ``folder.read``（v3 接口）。"""
        resp = await self._client.get(self._url("/view/folder/unique-paths"))
        resp.raise_for_status()
        return resp.json()

    async def folder_assets(self, path: str) -> list[dict]:
        """列出某文件夹下的直接子资产（非递归——子路径需分别调用）。"""
        resp = await self._client.get(
            self._url("/view/folder"), params={"path": path}
        )
        resp.raise_for_status()
        return resp.json()

    async def assets_under_folder(self, folder: str) -> list[dict]:
        """列出 ``folder`` 下全部资产（含子文件夹，聚合 view/folder）。"""
        prefix = folder.rstrip("/") + "/"
        paths = [p for p in await self.unique_folder_paths() if p == folder or p.startswith(prefix)]
        assets: list[dict] = []
        for p in paths:
            assets.extend(await self.folder_assets(p))
        # 按 id 去重并保持顺序
        seen: set[str] = set()
        unique = []
        for a in assets:
            if a["id"] not in seen:
                seen.add(a["id"])
                unique.append(a)
        return unique

    async def album_asset_ids(self, album_id: str, page_size: int = 250) -> set[str]:
        """通过 search 取相簿资产 ID（v3 移除了 GET /albums/{id}/assets）。

        ``nextPage`` 返回的是字符串，而 DTO 要求数字。
        """
        ids: set[str] = set()
        page = 1
        while True:
            resp = await self._client.post(
                self._url("/search/metadata"),
                json={"albumIds": [album_id], "page": page, "size": page_size},
            )
            resp.raise_for_status()
            block = resp.json().get("assets", {})
            ids |= {a["id"] for a in block.get("items", [])}
            nxt = block.get("nextPage")
            if not nxt:
                return ids
            page = int(nxt)

    async def list_users(self) -> list[dict]:
        """列出服务器用户。需要 ``user.read``。"""
        resp = await self._client.get(self._url("/users"))
        resp.raise_for_status()
        return resp.json()

    async def resolve_users(self, names_or_emails: list[str]) -> dict[str, str]:
        """把登录名/邮箱解析为用户 ID：{查询词: userId}。

        大小写不敏感地对 name 和 email 做子串匹配；
        没匹配到的查询词不会出现在结果里。
        """
        users = await self.list_users()
        found: dict[str, str] = {}
        for q in names_or_emails:
            ql = q.lower()
            for u in users:
                if ql in (u.get("name") or "").lower() or ql in (u.get("email") or "").lower():
                    found[q] = u["id"]
                    break
        return found

    async def share_album(self, album_id: str, user_ids: list[str], role: str = "editor") -> dict:
        """把用户加入相簿，保留现有成员。

        v3 坑：GET /albums 返回的 ``albumUsers`` 是
        ``[{"user": {"id": ...}, "role": "owner"|...}]`` 且包含 owner，
        但 PUT 的 payload 不能带 owner 条目（400 "Cannot add another
        owner"）。需要 ``albumUser.create``。
        """
        resp = await self._client.get(self._url(f"/albums/{album_id}"))
        resp.raise_for_status()
        detail = resp.json()
        merged = {
            au["user"]["id"]: au.get("role", "editor")
            for au in (detail.get("albumUsers") or [])
            if au.get("role") != "owner"
        }
        changed = False
        for uid in user_ids:
            if uid not in merged:
                merged[uid] = role
                changed = True
        if not changed:
            return detail
        resp = await self._client.put(
            self._url(f"/albums/{album_id}/users"),
            json={"albumUsers": [{"userId": uid, "role": r} for uid, r in merged.items()]},
        )
        resp.raise_for_status()
        return resp.json()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


def _guess_mime(path: Path) -> str:
    """按扩展名猜 MIME 类型。"""
    ext = path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".ts": "video/mp2t",
    }
    return mime_map.get(ext, "application/octet-stream")


def _format_datetime(value: datetime) -> str:
    """格式化为显式 UTC 的 ISO 8601 时间戳。"""
    if value.tzinfo is None:
        raise ValueError("file_timestamp must include timezone information")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
