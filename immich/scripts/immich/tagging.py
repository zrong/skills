"""批量视觉打标工作流：缩略图抓取、tag 字典维护、决策幂等应用与校验。

配合 ``references/tagging-playbook.md`` 使用。decisions JSON 每条形如：

- 逐资产：``{"id": "<uuid>", "tags": ["截图", "建议删除"], "description": null}``
- 按簇：  ``{"dir": "...", "tags": ["旅行"], "memberIds": ["<uuid>", ...]}``

所有写入幂等：重复执行不会产生重复 tag（服务端按资产去重），description
仅在资产当前描述为空时写入，绝不覆盖已有内容。
"""

import asyncio
import json
import random
from pathlib import Path

# 服务端 PUT /tags/{id}/assets 单次上限内取保守值
TAG_CHUNK = 200


def load_decisions(path: Path) -> list[dict]:
    """读取 decisions JSON（数组）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"decisions file must be a JSON array: {path}")
    return data


async def ensure_tags(
    client,
    taxonomy: dict[str, str | None],
    map_path: Path | None = None,
) -> dict[str, str]:
    """确保 taxonomy（name -> color|None）里的 tag 都存在，返回 name -> id。

    map_path 已存在且覆盖全部名字时直接复用（不重复请求）。
    """
    if map_path and map_path.exists():
        cached = json.loads(map_path.read_text(encoding="utf-8"))
        if all(name in cached for name in taxonomy):
            print(f"tags ready from cache: {map_path}")
            return cached

    existing = {t["name"]: t["id"] for t in await client.list_tags()}
    result: dict[str, str] = {}
    created = 0
    for name, color in taxonomy.items():
        if name in existing:
            result[name] = existing[name]
            continue
        tag = await client.create_tag(name, color)
        result[name] = tag["id"]
        created += 1
        print(f"created tag: {name}")
    if map_path:
        map_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    print(f"tags ready: {len(result)} (created {created})")
    return result


async def _fetch_one(
    client, sem: asyncio.Semaphore, idx: int, asset_id: str,
    outdir: Path, primary: str, fallback: str,
) -> tuple[str, bool]:
    async with sem:
        for size in (primary, fallback):
            try:
                data = await client.get_thumbnail(asset_id, size)
                (outdir / f"{idx:04d}_{asset_id[:8]}.jpg").write_bytes(data)
                return asset_id, True
            except Exception:
                continue
    return asset_id, False


async def fetch_thumbnails(
    client,
    items: list,
    outdir: Path,
    size: str = "thumbnail",
    workers: int = 8,
) -> tuple[int, list[str]]:
    """并行下载缩略图，命名 ``<序号>_<id前8>.jpg``。

    items 为 [{"id": ...}, ...] 或 ["<uuid>", ...]；先试 ``size``，
    失败自动回退另一档（thumbnail <-> preview）。两档都失败的 id 写入
    ``outdir/_failed.txt``（多为缩略图未生成的动态照片视频部分、损坏
    扫描件或断链记录）。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ids = [it if isinstance(it, str) else it["id"] for it in items]
    fallback = "preview" if size == "thumbnail" else "thumbnail"
    sem = asyncio.Semaphore(workers)
    results = await asyncio.gather(
        *(
            _fetch_one(client, sem, i, aid, outdir, size, fallback)
            for i, aid in enumerate(ids)
        )
    )
    failed = [aid for aid, ok in results if not ok]
    (outdir / "_failed.txt").write_text("\n".join(failed), encoding="utf-8")
    return len(ids) - len(failed), failed


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def apply_decisions(
    client,
    decisions: list[dict],
    tag_map: dict[str, str],
    log_path: Path | None = None,
) -> dict:
    """幂等应用打标决策，返回操作日志 dict。

    - tag 按名字分组、分块 PUT，逐条校验返回的 ``success``（外
      部库资产在只读挂载下会整体回滚，必须以回读/diff 为准，见
      references/api-pitfalls-and-debugging.md §10）
    - description 仅当资产当前描述为空时写入
    """
    tag_ids: dict[str, list[str]] = {}
    desc_ops: list[tuple[str, str]] = []
    skipped: list = []
    for d in decisions:
        ids = d.get("memberIds") or ([d["id"]] if d.get("id") else [])
        for name in d.get("tags") or []:
            if name not in tag_map:
                skipped.append((ids[:1], f"unknown tag {name}"))
                continue
            tag_ids.setdefault(name, []).extend(ids)
        if d.get("description") and d.get("id"):
            desc_ops.append((d["id"], d["description"]))

    log: dict = {"tag_ops": {}, "desc_ops": [], "errors": [], "skipped": skipped}

    for name, ids in tag_ids.items():
        done = already = err = 0
        for chunk in _chunks(ids, TAG_CHUNK):
            try:
                resp = await client.assign_assets_to_tag(tag_map[name], chunk)
            except Exception as e:
                err += len(chunk)
                log["errors"].append(f"tag {name}: {e}")
                continue
            ok = 0
            for e in resp:
                if not isinstance(e, dict):
                    continue
                if e.get("success"):
                    ok += 1
                elif e.get("error") == "duplicate":
                    already += 1  # 已带此 tag，幂等成功
                else:
                    err += 1
                    log["errors"].append(f"tag {name}: {e.get('error')} {e.get('id', '')[:8]}")
            # 响应里缺失的条目视为未生效
            miss = len(chunk) - len(resp)
            if miss:
                err += miss
                log["errors"].append(f"tag {name}: {miss}/{len(chunk)} 无响应条目")
            done += ok
        log["tag_ops"][name] = {
            "assigned": done, "already": already, "errors": err,
        }
        print(f"tag [{name}] assigned={done} already={already} errors={err}")

    for aid, desc in desc_ops:
        try:
            cur = await client.get_asset(aid)
            if (cur.get("exifInfo") or {}).get("description"):
                log["skipped"].append((aid, "desc already set"))
                continue
            await client.update_asset_description(aid, desc)
            log["desc_ops"].append({"id": aid, "description": desc})
        except Exception as e:
            log["errors"].append(f"desc {aid}: {e}")
    print(f"descriptions written={len(log['desc_ops'])}")

    if log_path:
        log_path.write_text(
            json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("log ->", log_path)
    if log["errors"]:
        print(f"ERRORS ({len(log['errors'])}):")
        for e in log["errors"][:20]:
            print(" ", e)
    return log


async def verify_decisions(
    client,
    decisions: list[dict],
    tag_map: dict[str, str],
    sample: int = 0,
) -> list[tuple[str, list[str]]]:
    """把 decisions 与服务端现状做 diff，返回缺失 (asset_id, tags) 列表。

    ``sample > 0`` 时随机抽查 N 个（资产级），否则全量校验——批量
    打标收尾必须跑一次全量，因为 sidecar 回滚和并发写入都会静默
    丢失部分赋值。
    """
    pairs: list[tuple[str, set[str]]] = []
    for d in decisions:
        want = set(d.get("tags") or [])
        if not want:
            continue
        for aid in d.get("memberIds") or [d["id"]]:
            pairs.append((aid, want))
    if sample and sample < len(pairs):
        pairs = random.sample(pairs, sample)

    sem = asyncio.Semaphore(20)

    async def _check(aid: str, want: set[str]):
        async with sem:
            asset = await client.get_asset(aid)
        have = {t["name"] for t in asset.get("tags", [])}
        miss = want - have
        return (aid, sorted(miss)) if miss else None

    results = await asyncio.gather(*(_check(a, w) for a, w in pairs))
    missing = [r for r in results if r]
    print(f"verify: checked={len(pairs)} missing={len(missing)}")
    for aid, m in missing[:20]:
        print("  MISS", aid[:8], m)
    return missing
