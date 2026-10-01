"""CLI entry point for immich skill."""

import argparse
import asyncio
import sys
from pathlib import Path

from immich.config import get_default_album, load_config
from immich.albums import build_plan, sync_album
from immich.client import ImmichClient
from immich.metadata import metadata_sidecar_path
from immich.uploader import ImmichUploader


def print_public_url(result: dict) -> None:
    """Print a public asset URL when the uploader returned one."""
    if public_url := result.get("public_url"):
        print(f"Public URL: {public_url}")


def main():
    parser = argparse.ArgumentParser(description="Upload to Immich")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # upload command
    up = sub.add_parser("upload", help="Upload local files")
    up.add_argument("files", nargs="+", type=Path, help="File paths to upload")
    up.add_argument("--album", "-a", help="Album name")
    description_source = up.add_mutually_exclusive_group()
    description_source.add_argument(
        "--description",
        help="Description for a single uploaded file",
    )
    description_source.add_argument(
        "--metadata-file",
        type=Path,
        help="video-downloader metadata sidecar for a single uploaded file",
    )
    up.add_argument(
        "--asset-time",
        choices=("upload", "source"),
        help="Override the configured asset timestamp policy",
    )

    # batch-upload command
    batch = sub.add_parser("batch-upload", help="Batch upload files from a directory")
    batch.add_argument("path", nargs="?", type=Path, default=Path.home() / "Downloads", help="Directory to upload from (default: ~/Downloads)")
    batch.add_argument("extensions", nargs="*", default=["mp4"], help="File extensions to upload (default: mp4)")
    batch.add_argument("--album", "-a", help="Album name")
    batch.add_argument("--no-delete", action="store_true", help="Do not delete local files after upload")
    batch.add_argument("--recursive", "-r", action="store_true", help="Recursively find files")
    batch.add_argument(
        "--asset-time",
        choices=("upload", "source"),
        help="Override the configured asset timestamp policy",
    )

    # init command
    sub.add_parser("init", help="Initialize and test config")

    # update-description command: patch asset_exif.description on an existing asset
    upd = sub.add_parser("update-description", help="Patch an asset's description (visible in Immich web UI)")
    upd.add_argument("asset_id", help="Immich asset UUID")
    upd.add_argument("description", help="New description text")

    # scan command: rescan an external library (NAS mounts have no inotify)
    scan_p = sub.add_parser("scan", help="Trigger external library scan and wait for it")
    scan_p.add_argument("--library-id", help="Library UUID (default: auto-detect by import path)")
    scan_p.add_argument("--import-path", default="/mnt/album", help="Import path used to find the library")
    scan_p.add_argument("--no-wait", action="store_true", help="Do not wait for the scan queue to drain")

    # folders command: browse server-side folder paths
    fol = sub.add_parser("folders", help="List asset folder paths with asset counts")
    fol.add_argument("--under", default="", help="Only show paths under this prefix")

    # album-plan / album-sync: folder -> album batch maintenance
    plan_p = sub.add_parser("album-plan", help="Plan albums for top-level folders under a root (dry-run)")
    plan_p.add_argument("--root", required=True, help="Folder root, e.g. /mnt/album/旅行")

    sync_p = sub.add_parser("album-sync", help="Create/verify/fill albums from folders (idempotent)")
    sync_p.add_argument("--root", required=True, help="Folder root, e.g. /mnt/album/旅行")
    sync_p.add_argument("--share-with", default="", help="Comma-separated user names/emails to share with")
    sync_p.add_argument("--role", default="editor", choices=("editor", "viewer"), help="Share role (default editor)")
    sync_p.add_argument("--apply", action="store_true", help="Actually write; without it only prints the plan")

    share_p = sub.add_parser("album-share", help="Share an existing album with users")
    share_p.add_argument("--album", required=True, help="Album name")
    share_p.add_argument("--with", dest="with_users", required=True, help="Comma-separated user names/emails")
    share_p.add_argument("--role", default="editor", choices=("editor", "viewer"), help="Share role (default editor)")

    dele = sub.add_parser("album-delete", help="Delete an album by name (assets are NOT deleted)")
    dele.add_argument("--album", required=True, help="Album name")

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


def init_and_test():
    """Load config and test API connection."""
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
    """Upload files in parallel."""
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
    """Batch upload files from a directory with given extensions."""
    if not directory.exists():
        print(f"Directory does not exist: {directory}")
        return
    if not directory.is_dir():
        print(f"Path is not a directory: {directory}")
        return

    # Build glob patterns for each extension
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

        # Upload in parallel
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

        # Delete successful uploads
        if success and not no_delete:
            print("\nDeleting uploaded files...")
            for path, result in success:
                path.unlink()
                metadata_sidecar_path(path).unlink(missing_ok=True)
                print(f"Deleted: {path.name}")
            print(f"Deleted {len(success)} files.")


async def update_description(asset_id: str, description: str):
    """Patch an asset's description via PATCH /api/assets/{id}."""
    load_config()
    async with ImmichClient() as client:
        result = await client.update_asset_description(asset_id, description)
        print(f"Updated description for {asset_id}: {result.get('id', 'OK')}")


async def cmd_scan(args):
    """Trigger an external-library scan (picks up NAS-side folder changes)."""
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
    """List folder paths and per-folder direct asset counts."""
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


if __name__ == "__main__":
    main()
