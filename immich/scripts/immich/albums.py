"""外部图库的 文件夹→相簿 规划与同步。

在 Immich v3.0.2 上随 /mnt/album/旅行 的批量相簿任务打磨而成
（2026-10）：40 个文件夹 → 40 个相簿、5751 个资产，1:1 校验一致
并共享给两位用户。

相簿命名规则：同年 `YYYYMMDD-MMDD地点`，跨年 `YYYYMMDD-YYYYMMDD地点`。
文件夹名里缺失的日期按以下优先级从照片时间推导：

1. 文件夹名中给出的日期部分（权威，不被照片覆盖）；
2. 真实 EXIF（`exifInfo.dateTimeOriginal`，取 `localDateTime` 的
   本地日历日），用众数 ±30 天聚簇去掉离群值；
3. `fileCreatedAt` 众数——弱证据（拷贝时间，例如多年后生成的海报帧），
   仅当 ≥50% 资产一致时采用，且不得把名称给出的起始日延后超过 7 天。
"""

import re
from collections import Counter
from datetime import date, datetime

_NAME_DATE_RE = re.compile(r"^((?:19|20)\d{2}[\d.\-—~至\s]*?)((?![\d.\-—~至]).*)$")
_YEAR_MONTH_RE = re.compile(r"^((?:19|20)\d{2})[.\-]?(\d{2})")


def parse_name(name: str) -> tuple[date | None, date | None, str]:
    """从文件夹/相簿名解析 ``(start, end, location)``。

    可识别的日期写法（起始与结束均适用）：``2002.07``、``2005.01.11``、
    ``20080708``（紧凑）、``2014.0805``（紧凑月日）；结束部分还支持
    ``0914``（MMDD）、``08.03``（MM.DD）、``22``（起始月的日）。
    精度不足的部分返回 None，交给调用方从照片推导。
    """
    s = name.strip()
    m = _NAME_DATE_RE.match(s)
    if not m:
        return None, None, s
    datepart, loc = m.group(1).strip(), m.group(2).strip()

    segs = re.split(r"[-—~至]", datepart)
    start_raw = segs[0].strip()
    end_raw = segs[1].strip() if len(segs) > 1 and segs[1].strip() else None

    def parse_full(raw: str):
        raw = raw.strip().rstrip(".")
        toks = raw.replace(".", " ").split()
        if not toks:
            return None, None, None
        if len(toks) == 1 and len(toks[0]) == 8 and toks[0].isdigit():
            t = toks[0]
            return int(t[:4]), int(t[4:6]), int(t[6:8])
        y = int(toks[0]) if len(toks[0]) == 4 and toks[0].isdigit() else None
        mo = d = None
        if len(toks) > 1 and toks[1].isdigit():
            t = toks[1]
            if len(t) <= 2:
                mo = int(t)
            else:  # 0805 紧凑 MMDD
                mo, d = int(t[:-2]), int(t[-2:])
        if len(toks) > 2 and toks[2].isdigit():
            d = int(toks[2])
        return y, mo, d

    def mk(y, mo, d):
        try:
            return date(y, mo, d)
        except (TypeError, ValueError):
            return None

    sy, smo, sd = parse_full(start_raw)
    start = mk(sy, smo, sd) if (smo and sd) else None

    end = None
    if end_raw:
        if re.fullmatch(r"\d{8}", end_raw):  # 20250102 紧凑完整日期
            t = end_raw
            end = mk(int(t[:4]), int(t[4:6]), int(t[6:8]))
        elif re.fullmatch(r"(19|20)\d{2}(\.\d{1,2}){0,2}", end_raw):
            ey, emo, ed = parse_full(end_raw)
            end = mk(ey or sy, emo, ed) if (emo and ed) else None
        elif re.fullmatch(r"\d{3,4}", end_raw):
            t = end_raw.zfill(4)
            end = mk(sy, int(t[:2]), int(t[2:4]))
        elif re.fullmatch(r"\d{1,2}(\.\d{1,2})?", end_raw):
            emo, _, ed = end_raw.partition(".")
            if ed:
                end = mk(sy, int(emo), int(ed))
            else:
                end = mk(sy, smo, int(emo)) or mk(sy, int(emo), 1)
                if end and start and end < start:
                    end = None  # 裸数字有歧义，交给照片推导
    return start, end, loc


def album_span(start: date, end: date) -> str:
    return f"{start:%Y%m%d}-{end:%Y%m%d}" if start.year != end.year else f"{start:%Y%m%d}-{end:%m%d}"


def asset_date(asset: dict) -> tuple[date | None, str]:
    """返回资产的 ``(日期, "exif"|"file"|"" )``。

    v3 坑：顶层 ``dateTimeOriginal`` 通常为空，真实 EXIF 在
    ``exifInfo.dateTimeOriginal``。``localDateTime`` 是本地日历日渲染，
    只有存在真实 EXIF 时才可信——否则是被 fileCreatedAt 回填的值。
    """
    def parse(v):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except (ValueError, AttributeError):
            return None

    ei = asset.get("exifInfo") or {}
    if ei.get("dateTimeOriginal"):
        return parse(asset.get("localDateTime") or ei["dateTimeOriginal"]), "exif"
    return parse(asset.get("fileCreatedAt")), "file"


def modal_date(dates: list[date]) -> tuple[date | None, float]:
    if not dates:
        return None, 0.0
    modal, count = Counter(dates).most_common(1)[0]
    return modal, count / len(dates)


def cluster_bounds(dates: list[date]) -> tuple[date, date]:
    """去掉离群值后的 (min, max)：众数 ±30 天窗口，否则 P5/P95。

    处理相机时钟错乱的单张（日期偏到几个月后）和工具生成的海报帧
    （EXIF 带生成日期）。
    """
    modal, freq = modal_date(dates)
    if modal and freq >= 0.5:
        near = [d for d in dates if abs((d - modal).days) <= 30]
        return min(near), max(near)
    if len(dates) >= 10:
        return dates[int(len(dates) * 0.05)], dates[min(len(dates) - 1, int(len(dates) * 0.95))]
    return dates[0], dates[-1]


def derive_album_name(folder: str, assets: list[dict]) -> dict:
    """为一个文件夹的资产计算相簿名及元数据。

    返回 dict，含 album_name/start/end/flags/asset_ids。
    """
    dated = [(d, src) for d, src in (asset_date(a) for a in assets) if d]
    exif_dates = sorted(d for d, src in dated if src == "exif")
    file_dates = sorted(d for d, src in dated if src == "file")
    n_start, n_end, loc = parse_name(folder)

    flags: list[str] = []
    start = end = None
    # start：名称 > 真实 EXIF（聚簇） > 文件日期众数
    if n_start:
        start = n_start
    elif exif_dates:
        start = cluster_bounds(exif_dates)[0]
    else:
        modal, freq = modal_date(file_dates)
        if modal and freq >= 0.5:
            start = modal

    # end：名称 > 真实 EXIF（聚簇） > 文件日期众数（有前提）
    if n_end:
        end = n_end
    elif exif_dates:
        end = cluster_bounds(exif_dates)[1]
    elif start:
        modal, freq = modal_date(file_dates)
        if modal and freq >= 0.5:
            if modal != start and (modal - start).days > 7:
                pass  # 全部同日拷贝且晚于名称起始>7天：是拷贝日期，不是行程结束
            else:
                end = modal
        if end is None:
            end = start

    if start and end and end < start:
        end = start
    if n_start and end and 14 < (end - n_start).days:
        flags.append(f"结束日期来自照片({end})，与名称起始({n_start})相隔{(end - n_start).days}天，请复核")
    ym = _YEAR_MONTH_RE.match(folder)
    if ym:
        y, mo = int(ym.group(1)), int(ym.group(2))
        for bound, label in ((start, "start"), (end, "end")):
            from_name = (label == "start" and n_start) or (label == "end" and n_end)
            if bound and not from_name and (bound.year != y or bound.month != mo):
                flags.append(f"{label}{bound}与文件夹名{y}年{mo}月不符")

    name = album_span(start, end) + loc if (start and end) else None
    return {
        "folder": folder,
        "album_name": name,
        "location": loc,
        "start": str(start),
        "end": str(end),
        "flags": flags,
        "asset_ids": sorted({a["id"] for a in assets}),
    }


def normalize_name(name: str) -> str:
    return re.sub(r"[.\s\-—_]", "", name or "")


def match_album(plan_name: str, albums: list[dict]) -> dict | None:
    """查找与计划名匹配的现有相簿。

    先做去分隔符的精确匹配，再按解析出的（日期区间 + 地点）匹配，
    容忍用户建相簿时用了不同的分隔符。
    """
    norm = normalize_name(plan_name)
    for al in albums:
        if normalize_name(al["albumName"]) == norm:
            return al
    s, e, loc = parse_name(plan_name)
    for al in albums:
        s2, e2, loc2 = parse_name(al["albumName"])
        if s and s2 and e and e2 and s == s2 and e == e2 and normalize_name(loc) == normalize_name(loc2):
            return al
    return None


async def build_plan(client, root: str) -> list[dict]:
    """为 ``root`` 下每个顶层文件夹规划相簿。"""
    paths = await client.unique_folder_paths()
    prefix = root.rstrip("/") + "/"
    under = [p for p in paths if p.startswith(prefix)]
    tops = sorted({p[len(prefix):].split("/")[0] for p in under})

    albums = await client.get_albums()
    plan = []
    for top in tops:
        assets = await client.assets_under_folder(prefix + top)
        if not assets:
            continue
        entry = derive_album_name(top, assets)
        entry["root"] = root.rstrip("/")
        entry["asset_count"] = len(entry["asset_ids"])
        al = match_album(entry["album_name"], albums) if entry["album_name"] else None
        entry["matched_album"] = {"id": al["id"], "name": al["albumName"]} if al else None
        plan.append(entry)
    return plan


async def sync_album(client, entry: dict, share_user_ids: list[str] | None = None, role: str = "editor") -> dict:
    """按计划条目 创建/补齐/校验 一个相簿。幂等。"""
    if not entry["album_name"]:
        return {"album": None, "status": "unnamed", "entry": entry}

    albums = await client.get_albums()
    al = match_album(entry["album_name"], albums)
    if al:
        album_id, action = al["id"], "reused"
    else:
        created = await client.create_album(
            entry["album_name"],
            description=f"来源文件夹: {entry.get('root', '')}/{entry['folder']}",
        )
        album_id, action = created["id"], "created"

    current = await client.album_asset_ids(album_id)
    missing = [i for i in entry["asset_ids"] if i not in current]
    added = 0
    for i in range(0, len(missing), 500):
        await client.add_assets_to_album(album_id, missing[i:i + 500])
        added += len(missing[i:i + 500])

    shared = None
    if share_user_ids:
        await client.share_album(album_id, share_user_ids, role=role)
        shared = f"{len(share_user_ids)} users ({role})"

    return {
        "album": entry["album_name"], "status": action, "album_id": album_id,
        "folder_assets": len(entry["asset_ids"]), "already": len(current), "added": added, "shared": shared,
    }
