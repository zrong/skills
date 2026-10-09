"""immich skill 的 CLI 入口。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from immich.config import get_default_album, load_config
from immich.albums import build_plan, sync_album
from immich.client import ImmichClient
from immich.metadata import metadata_sidecar_path
from immich.uploader import ImmichUploader
from immich import tagging


def print_public_url(result: dict) -> None:
    """上传结果带 public_url 时打印公开链接。"""
    if public_url := result.get("public_url"):
        print(f"Public URL: {public_url}")


def main():
    parser = argparse.ArgumentParser(description="Immich 上传与管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # upload command
    up = sub.add_parser("upload", help="上传本地文件")
    up.add_argument("files", nargs="+", type=Path, help="File paths to upload")
    up.add_argument("--album", "-a", help="相簿名称")
    description_source = up.add_mutually_exclusive_group()
    description_source.add_argument(
        "--description",
        help="单个上传文件的描述",
    )
    description_source.add_argument(
        "--metadata-file",
        type=Path,
        help="单文件上传使用的 video-downloader 元数据侧车",
    )
    up.add_argument(
        "--asset-time",
        choices=("upload", "source"),
        help="覆盖配置的资产时间戳策略",
    )

    # batch-upload command
    batch = sub.add_parser("batch-upload", help="批量上传目录下的文件")
    batch.add_argument("path", nargs="?", type=Path, default=Path.home() / "Downloads", help="上传目录（默认 ~/Downloads）")
    batch.add_argument("extensions", nargs="*", default=["mp4"], help="上传的文件扩展名（默认 mp4）")
    batch.add_argument("--album", "-a", help="相簿名称")
    batch.add_argument("--no-delete", action="store_true", help="上传后不删除本地文件")
    batch.add_argument("--recursive", "-r", action="store_true", help="递归查找文件")
    batch.add_argument(
        "--asset-time",
        choices=("upload", "source"),
        help="覆盖配置的资产时间戳策略",
    )

    # init command
    sub.add_parser("init", help="初始化并测试配置")

    # update-description command: patch asset_exif.description on an existing asset
    upd = sub.add_parser("update-description", help="修改资产描述（网页端可见）")
    upd.add_argument("asset_id", help="Immich 资产 UUID")
    upd.add_argument("description", help="新的描述文本")

    # scan command: rescan an external library (NAS mounts have no inotify)
    scan_p = sub.add_parser("scan", help="触发外部图库扫描并等待完成")
    scan_p.add_argument("--library-id", help="图库 UUID（默认按导入路径自动识别）")
    scan_p.add_argument("--import-path", default="/mnt/album", help="用于定位图库的导入路径")
    scan_p.add_argument("--no-wait", action="store_true", help="不等待扫描队列排空")

    # folders command: browse server-side folder paths
    fol = sub.add_parser("folders", help="列出资产文件夹路径及资产数")
    fol.add_argument("--under", default="", help="只显示该前缀下的路径")

    # album-plan / album-sync: folder -> album batch maintenance
    plan_p = sub.add_parser("album-plan", help="规划根目录下各顶层文件夹的相簿（dry-run）")
    plan_p.add_argument("--root", required=True, help="文件夹根路径，如 /mnt/album/旅行")

    sync_p = sub.add_parser("album-sync", help="从文件夹创建/校验/补齐相簿（幂等）")
    sync_p.add_argument("--root", required=True, help="文件夹根路径，如 /mnt/album/旅行")
    sync_p.add_argument("--share-with", default="", help="共享的用户名/邮箱，逗号分隔")
    sync_p.add_argument("--role", default="editor", choices=("editor", "viewer"), help="共享角色（默认 editor）")
    sync_p.add_argument("--apply", action="store_true", help="实际写入；不带则只打印计划")

    share_p = sub.add_parser("album-share", help="把现有相簿共享给用户")
    share_p.add_argument("--album", required=True, help="相簿名称")
    share_p.add_argument("--with", dest="with_users", required=True, help="用户名/邮箱，逗号分隔")
    share_p.add_argument("--role", default="editor", choices=("editor", "viewer"), help="共享角色（默认 editor）")

    dele = sub.add_parser("album-delete", help="按名称删除相簿（不删除资产本身）")
    dele.add_argument("--album", required=True, help="相簿名称")

    # tag-fetch / tags-ensure / tag-apply / tag-verify: 批量视觉打标工作流
    tf = sub.add_parser("tag-fetch", help="批量下载缩略图（打标工作流）")
    tf.add_argument("batch", type=Path, help='清单 JSON：[{"id": ...}, ...] 或 ["<uuid>", ...]')
    tf.add_argument("outdir", type=Path, help="缩略图输出目录")
    tf.add_argument("--size", choices=("thumbnail", "preview"), default="thumbnail",
                    help="首选档位（thumbnail≈250px 分类够用，preview≈1440px 需读文字时用）")
    tf.add_argument("--workers", type=int, default=8, help="并行下载数")

    te = sub.add_parser("tags-ensure", help="确保 tag 字典存在并输出 name->id 映射")
    te.add_argument("--names", required=True, help="逗号分隔的 tag 名")
    te.add_argument("--colors", default="", help="与 names 对应的十六进制颜色，逗号分隔（可留空）")
    te.add_argument("--map-out", type=Path, default=Path("tag_map.json"), help="映射输出文件")

    ta = sub.add_parser("tag-apply", help="幂等应用打标决策（tag + 描述）")
    ta.add_argument("decisions", type=Path, help="decisions JSON（逐资产或按簇，见 tagging-playbook）")
    ta.add_argument("--tag-map", type=Path, default=None, help="tag_map.json（默认取 decisions 同目录或 cwd）")
    ta.add_argument("--log", dest="log_path", type=Path, default=None,
                    help="操作日志输出路径（默认 decisions 同目录 <名>.log.json）")

    tv = sub.add_parser("tag-verify", help="decisions 与服务端现状 diff（批量打标收尾必跑）")
    tv.add_argument("decisions", type=Path, help="decisions JSON")
    tv.add_argument("--tag-map", type=Path, default=None, help="tag_map.json（默认取 decisions 同目录或 cwd）")
    tv.add_argument("--sample", type=int, default=0, help="随机抽查 N 个（0=全量，默认全量）")

    args = parser.parse_args()

    if args.cmd == "init":
        init_and_test()
    elif args.cmd == "upload":
        if args.description is not None and len(args.files) != 1:
            parser.error("--description requires exactly one file")
        if args.metadata_file is not None and len(args.files) != 1:
            parser.error("--metadata-file requires exactly one file")
        asyncio.run(
            upload_files(
                args.files,
                args.album,
                args.description,
                args.asset_time,
                args.metadata_file,
            )
        )
    elif args.cmd == "batch-upload":
        asyncio.run(
            batch_upload_files(
                args.path,
                args.extensions,
                args.album,
                args.no_delete,
                args.recursive,
                args.asset_time,
            )
        )
    elif args.cmd == "update-description":
        asyncio.run(update_description(args.asset_id, args.description))
    elif args.cmd == "scan":
        asyncio.run(cmd_scan(args))
    elif args.cmd == "folders":
        asyncio.run(cmd_folders(args))
    elif args.cmd == "album-plan":
        asyncio.run(cmd_album_plan(args))
    elif args.cmd == "album-sync":
        asyncio.run(cmd_album_sync(args))
    elif args.cmd == "album-share":
        asyncio.run(cmd_album_share(args))
    elif args.cmd == "album-delete":
        asyncio.run(cmd_album_delete(args))
    elif args.cmd == "tag-fetch":
        asyncio.run(cmd_tag_fetch(args))
    elif args.cmd == "tags-ensure":
        asyncio.run(cmd_tags_ensure(args))
    elif args.cmd == "tag-apply":
        asyncio.run(cmd_tag_apply(args))
    elif args.cmd == "tag-verify":
        asyncio.run(cmd_tag_verify(args))


def init_and_test():
    """加载配置并测试 API 连接。"""
    load_config()
    default_album = get_default_album()
    print(f"Config loaded. Default album: {default_album or '(none)'}")

    try:
        async def _probe():
            async with ImmichClient() as client:
                return await client.get_albums()

        albums = asyncio.run(_probe())
        print(f"Connected! Found {len(albums)} albums.")
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)


async def upload_files(
    paths: list[Path],
    album_name: str | None,
    description: str | None = None,
    asset_time_source: str | None = None,
    metadata_file: Path | None = None,
):
    """并行上传多个文件。"""
    if description is not None and len(paths) != 1:
        raise ValueError("description requires exactly one file")
    load_config()
    album_name = album_name or get_default_album()

    async with ImmichClient() as client:
        uploader = ImmichUploader(client, asset_time_source=asset_time_source)

        if len(paths) > 1:
            print(f"Uploading {len(paths)} files in parallel...")
            results = await uploader.upload_files(paths, album_name)
            for path, result in zip(paths, results):
                if isinstance(result, Exception):
                    print(f"FAIL {path}: {result}")
                else:
                    print(f"OK   {path}: {result.get('id')}")
                    print_public_url(result)
        else:
            upload_options = {"description": description}
            if metadata_file is not None:
                upload_options["metadata_path"] = metadata_file
            result = await uploader.upload_file(paths[0], album_name, **upload_options)
            print(f"Uploaded: {result.get('id')}")
            print_public_url(result)


async def batch_upload_files(
    directory: Path,
    extensions: list[str],
    album_name: str | None,
    no_delete: bool,
    recursive: bool,
    asset_time_source: str | None = None,
):
    """按扩展名批量上传目录下的文件。"""
    if not directory.exists():
        print(f"Directory does not exist: {directory}")
        return
    if not directory.is_dir():
        print(f"Path is not a directory: {directory}")
        return

    # 为每个扩展名构造 glob 模式
    patterns = [f"*.{ext.lstrip('.')}" for ext in extensions]
    files: list[Path] = []
    for pattern in patterns:
        if recursive:
            files.extend(directory.rglob(pattern))
        else:
            files.extend(directory.glob(pattern))

    if not files:
        print(f"No files found in {directory} with extensions: {extensions}")
        return

    print(f"Found {len(files)} files in {directory}")
    album_name = album_name or get_default_album()
    if album_name:
        print(f"Using album: {album_name}")

    load_config()

    async with ImmichClient() as client:
        uploader = ImmichUploader(client, asset_time_source=asset_time_source)

        # 并行上传
        results = await uploader.upload_files(files, album_name)

        success = []
        failed = []

        for path, result in zip(files, results):
            if isinstance(result, Exception):
                failed.append((path, result))
                print(f"FAIL: {path.name} - {result}")
            else:
                success.append((path, result))
                print(f"OK: {path.name} -> {result.get('id')}")
                print_public_url(result)

        print(f"\nSummary: {len(success)} succeeded, {len(failed)} failed")

        # 删除上传成功的本地文件
        if success and not no_delete:
            print("\nDeleting uploaded files...")
            for path, result in success:
                path.unlink()
                metadata_sidecar_path(path).unlink(missing_ok=True)
                print(f"Deleted: {path.name}")
            print(f"Deleted {len(success)} files.")


async def update_description(asset_id: str, description: str):
    """通过 PATCH /api/assets/{id} 修改资产描述。"""
    load_config()
    async with ImmichClient() as client:
        result = await client.update_asset_description(asset_id, description)
        print(f"Updated description for {asset_id}: {result.get('id', 'OK')}")


async def cmd_scan(args):
    """触发外部图库扫描（感知 NAS 侧的文件夹变更）。"""
    load_config()
    async with ImmichClient() as client:
        lib_id = args.library_id
        if not lib_id:
            lib = await client.find_library_by_import_path(args.import_path)
            if not lib:
                print(f"No library found for import path {args.import_path}", file=sys.stderr)
                sys.exit(1)
            lib_id = lib["id"]
            print(f"Library: {lib.get('name')} ({lib_id})")
        await client.scan_library(lib_id)
        print("Scan queued (204).")
        if args.no_wait:
            return
        print("Waiting for scan queue to drain...", flush=True)
        ok = await client.wait_for_library_scan()
        print("Scan finished." if ok else "Timed out waiting for scan; check /api/jobs.")


async def cmd_folders(args):
    """列出文件夹路径及各目录的直接资产数。"""
    load_config()
    async with ImmichClient() as client:
        paths = await client.unique_folder_paths()
        for p in sorted(paths):
            if args.under and not p.startswith(args.under.rstrip("/") + "/") and p != args.under:
                continue
            assets = await client.folder_assets(p)
            print(f"{len(assets):>5}  {p}")


def _print_plan(plan):
    print(f"{'文件夹':<36} {'相簿名':<32} {'资产':>4} {'匹配':<14} 备注")
    print("-" * 110)
    for p in sorted(plan, key=lambda x: x["start"] or "9999"):
        m = (p["matched_album"]["name"] or "")[:12] if p["matched_album"] else "新建"
        print(f"{p['folder']:<36} {str(p['album_name']):<32} {p['asset_count']:>4} {m:<14} {'; '.join(p['flags'])}")
    print("-" * 110)
    print(f"共 {len(plan)} 个计划 / {sum(p['asset_count'] for p in plan)} 个资产；"
          f"复用 {sum(1 for p in plan if p['matched_album'])}，"
          f"无法命名 {sum(1 for p in plan if not p['album_name'])}")


async def cmd_album_plan(args):
    load_config()
    async with ImmichClient() as client:
        plan = await build_plan(client, args.root)
        _print_plan(plan)


async def cmd_album_sync(args):
    load_config()
    async with ImmichClient() as client:
        plan = await build_plan(client, args.root)
        _print_plan(plan)
        if not args.apply:
            print("\n(dry-run — pass --apply to create/fill/share)")
            return
        share_ids = []
        if args.share_with:
            queries = [q.strip() for q in args.share_with.split(",") if q.strip()]
            resolved = await client.resolve_users(queries)
            missing = [q for q in queries if q not in resolved]
            if missing:
                print(f"!! 未找到用户，跳过共享: {missing}", file=sys.stderr)
            share_ids = list(resolved.values())
        failures = []
        for entry in sorted(plan, key=lambda x: x["start"] or "9999"):
            try:
                res = await sync_album(client, entry, share_ids or None, role=args.role)
            except Exception as e:
                failures.append((entry["folder"], str(e)))
                print(f"FAIL {entry['folder']}: {e}", file=sys.stderr)
                continue
            if res["status"] == "unnamed":
                print(f"SKIP {entry['folder']}: 无法确定相簿名（照片无日期信息）")
            else:
                print(f"[{res['status']}] {res['album']:<32} 原有={res['already']} 新增={res['added']} "
                      f"共享={res['shared'] or '-'}")
        print(f"\n完成；失败 {len(failures)} 项" + ("：" + "; ".join(f"{n}: {m}" for n, m in failures) if failures else ""))


async def cmd_album_share(args):
    load_config()
    async with ImmichClient() as client:
        albums = await client.get_albums()
        al = next((a for a in albums if a["albumName"] == args.album), None)
        if not al:
            print(f"Album not found: {args.album}", file=sys.stderr)
            sys.exit(1)
        queries = [q.strip() for q in args.with_users.split(",") if q.strip()]
        resolved = await client.resolve_users(queries)
        missing = [q for q in queries if q not in resolved]
        if missing:
            print(f"!! 未找到用户: {missing}", file=sys.stderr)
            sys.exit(1)
        await client.share_album(al["id"], list(resolved.values()), role=args.role)
        print(f"Shared '{args.album}' with {queries} as {args.role}.")


async def cmd_album_delete(args):
    load_config()
    async with ImmichClient() as client:
        albums = await client.get_albums()
        al = next((a for a in albums if a["albumName"] == args.album), None)
        if not al:
            print(f"Album not found: {args.album}", file=sys.stderr)
            sys.exit(1)
        await client.delete_album(al["id"])
        print(f"Deleted album '{args.album}' ({al.get('assetCount', '?')} assets released, files kept).")


def _resolve_tag_map(decisions_path: Path, tag_map: Path | None) -> Path:
    """tag 映射文件查找顺序：显式指定 > decisions 同目录 > cwd。"""
    if tag_map:
        return tag_map
    local = decisions_path.parent / "tag_map.json"
    if local.exists():
        return local
    return Path("tag_map.json")


async def cmd_tag_fetch(args):
    """批量下载缩略图（打标工作流第一步）。"""
    load_config()
    items = json.loads(args.batch.read_text(encoding="utf-8"))
    async with ImmichClient() as client:
        ok, failed = await tagging.fetch_thumbnails(
            client, items, args.outdir, size=args.size, workers=args.workers
        )
    print(f"downloaded={ok} failed={len(failed)} outdir={args.outdir}")
    if failed:
        print("failed ids:", ", ".join(failed[:10]) + (" ..." if len(failed) > 10 else ""))


async def cmd_tags_ensure(args):
    """确保 tag 字典存在，写 name->id 映射。"""
    load_config()
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    colors = [c.strip() for c in args.colors.split(",") if c.strip()]
    if colors and len(colors) != len(names):
        print("--colors 数量必须与 --names 一致", file=sys.stderr)
        sys.exit(1)
    taxonomy = {n: (colors[i] if colors else None) for i, n in enumerate(names)}
    async with ImmichClient() as client:
        await tagging.ensure_tags(client, taxonomy, map_path=args.map_out)


async def cmd_tag_apply(args):
    """幂等应用打标决策。"""
    load_config()
    decisions = tagging.load_decisions(args.decisions)
    map_path = _resolve_tag_map(args.decisions, args.tag_map)
    tag_map = json.loads(map_path.read_text(encoding="utf-8"))
    log_path = args.log_path or args.decisions.with_name(
        args.decisions.stem + ".log.json"
    )
    async with ImmichClient() as client:
        await tagging.apply_decisions(client, decisions, tag_map, log_path=log_path)


async def cmd_tag_verify(args):
    """decisions 与服务端现状 diff。"""
    load_config()
    decisions = tagging.load_decisions(args.decisions)
    map_path = _resolve_tag_map(args.decisions, args.tag_map)
    tag_map = json.loads(map_path.read_text(encoding="utf-8"))
    async with ImmichClient() as client:
        missing = await tagging.verify_decisions(
            client, decisions, tag_map, sample=args.sample
        )
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
