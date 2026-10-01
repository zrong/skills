"""Folder-to-album planning and syncing for external libraries.

Developed against Immich v3.0.2 while batch-creating travel albums from
``/mnt/album/旅行`` (2026-10): 40 folders -> 40 albums, 5751 assets,
verified 1:1 and shared with two users.

Album naming rule: ``YYYYMMDD-MMDD<location>`` within a year,
``YYYYMMDD-YYYYMMDD<location>`` across years. Dates missing from the
folder name are derived from photo timestamps with this priority:

1. date parts present in the folder name (authoritative);
2. real EXIF (``exifInfo.dateTimeOriginal``, via the local-calendar
   ``localDateTime`` field), outlier-trimmed with a modal +/-30d cluster;
3. ``fileCreatedAt`` modal date — weak evidence (copy time, e.g. poster
   frames generated years later), only when >=50% of assets agree, and
   never extended more than 7 days past a name-given start.
"""

import re
from collections import Counter
from datetime import date, datetime

_NAME_DATE_RE = re.compile(r"^((?:19|20)\d{2}[\d.\-—~至\s]*?)((?![\d.\-—~至]).*)$")
_YEAR_MONTH_RE = re.compile(r"^((?:19|20)\d{2})[.\-]?(\d{2})")


def parse_name(name: str) -> tuple[date | None, date | None, str]:
    """Parse ``(start, end, location)`` from a folder/album name.

    Understood date formats (start and end): ``2002.07``, ``2005.01.11``,
    ``20080708`` (compact), ``2014.0805`` (compact month+day), end parts
    ``0914`` (MMDD), ``08.03`` (MM.DD), ``22`` (day of start month).
    Missing precision returns None for the caller to derive from photos.
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
            else:  # 0805 compact MMDD
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
        if re.fullmatch(r"\d{8}", end_raw):  # 20250102 compact full date
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
                    end = None  # ambiguous bare number; leave to photos
    return start, end, loc


def album_span(start: date, end: date) -> str:
    return f"{start:%Y%m%d}-{end:%Y%m%d}" if start.year != end.year else f"{start:%Y%m%d}-{end:%m%d}"


def asset_date(asset: dict) -> tuple[date | None, str]:
    """Return ``(date, "exif"|"file"|"" )`` for an asset.

    v3 pitfall: the top-level ``dateTimeOriginal`` is usually empty; the
    real EXIF lives in ``exifInfo.dateTimeOriginal``. ``localDateTime``
    is Immich's local-calendar rendering and is only trustworthy when real
    EXIF exists — otherwise it is a fileCreatedAt backfill.
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
    """Outlier-trimmed (min, max): modal +/-30d window, else P5/P95.

    Handles camera-clock strays (one photo months off) and tool-generated
    poster frames whose EXIF carries the generation date.
    """
    modal, freq = modal_date(dates)
    if modal and freq >= 0.5:
        near = [d for d in dates if abs((d - modal).days) <= 30]
        return min(near), max(near)
    if len(dates) >= 10:
        return dates[int(len(dates) * 0.05)], dates[min(len(dates) - 1, int(len(dates) * 0.95))]
    return dates[0], dates[-1]


def derive_album_name(folder: str, assets: list[dict]) -> dict:
    """Compute the album name and metadata for one folder's assets.

    Returns dict with album_name/start/end/flags/asset_ids.
    """
    dated = [(d, src) for d, src in (asset_date(a) for a in assets) if d]
    exif_dates = sorted(d for d, src in dated if src == "exif")
    file_dates = sorted(d for d, src in dated if src == "file")
    n_start, n_end, loc = parse_name(folder)

    flags: list[str] = []
    start = end = None
    if n_start:
        start = n_start
    elif exif_dates:
        start = cluster_bounds(exif_dates)[0]
    else:
        modal, freq = modal_date(file_dates)
        if modal and freq >= 0.5:
            start = modal

    if n_end:
        end = n_end
    elif exif_dates:
        end = cluster_bounds(exif_dates)[1]
    elif start:
        modal, freq = modal_date(file_dates)
        if modal and freq >= 0.5:
            if modal != start and (modal - start).days > 7:
                pass  # all copied same day >7d after the named start: copy date, not trip end
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
    """Find an existing album matching a planned name.

    Exact normalized match first, then (startDate range + location) parsed
    match, tolerating different separators the user typed.
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
    """Plan albums for every top-level folder under ``root``."""
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
    """Create/fill/verify one album from a plan entry. Idempotent."""
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
