"""CLI entry point for immich skill."""

import argparse
import asyncio
import sys
from pathlib import Path

from immich.config import get_default_album, load_config
from immich.client import ImmichClient
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

    # upload-url command
    url = sub.add_parser("upload-url", help="Download and upload from URL")
    url.add_argument("url", help="Remote URL to download and upload")
    url.add_argument("--album", "-a", help="Album name")

    # batch-upload command
    batch = sub.add_parser("batch-upload", help="Batch upload files from a directory")
    batch.add_argument("path", nargs="?", type=Path, default=Path.home() / "Downloads", help="Directory to upload from (default: ~/Downloads)")
    batch.add_argument("extensions", nargs="*", default=["mp4"], help="File extensions to upload (default: mp4)")
    batch.add_argument("--album", "-a", help="Album name")
    batch.add_argument("--no-delete", action="store_true", help="Do not delete local files after upload")
    batch.add_argument("--recursive", "-r", action="store_true", help="Recursively find files")

    # init command
    sub.add_parser("init", help="Initialize and test config")

    # update-description command: patch asset_exif.description on an existing asset
    upd = sub.add_parser("update-description", help="Patch an asset's description (visible in Immich web UI)")
    upd.add_argument("asset_id", help="Immich asset UUID")
    upd.add_argument("description", help="New description text")

    args = parser.parse_args()

    if args.cmd == "init":
        init_and_test()
    elif args.cmd == "upload":
        asyncio.run(upload_files(args.files, args.album))
    elif args.cmd == "upload-url":
        asyncio.run(upload_single_url(args.url, args.album))
    elif args.cmd == "batch-upload":
        asyncio.run(batch_upload_files(args.path, args.extensions, args.album, args.no_delete, args.recursive))
    elif args.cmd == "update-description":
        asyncio.run(update_description(args.asset_id, args.description))


def init_and_test():
    """Load config and test API connection."""
    load_config()
    default_album = get_default_album()
    print(f"Config loaded. Default album: {default_album or '(none)'}")

    try:
        with ImmichClient() as client:
            albums = asyncio.run(client.get_albums())
            print(f"Connected! Found {len(albums)} albums.")
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)


async def upload_files(paths: list[Path], album_name: str | None):
    """Upload files in parallel."""
    load_config()
    album_name = album_name or get_default_album()

    async with ImmichClient() as client:
        uploader = ImmichUploader(client)

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
            result = await uploader.upload_file(paths[0], album_name)
            print(f"Uploaded: {result.get('id')}")
            print_public_url(result)


async def upload_single_url(url: str, album_name: str | None):
    """Download URL and upload to Immich."""
    load_config()
    album_name = album_name or get_default_album()

    async with ImmichClient() as client:
        uploader = ImmichUploader(client)
        print(f"Downloading {url} via yt-dlp...")
        result = await uploader.upload_url(url, album_name)
        print(f"Uploaded: {result.get('id')}")
        print_public_url(result)


async def batch_upload_files(directory: Path, extensions: list[str], album_name: str | None, no_delete: bool, recursive: bool):
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
        uploader = ImmichUploader(client)

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
                print(f"Deleted: {path.name}")
            print(f"Deleted {len(success)} files.")


async def update_description(asset_id: str, description: str):
    """Patch an asset's description via PATCH /api/assets/{id}."""
    load_config()
    async with ImmichClient() as client:
        result = await client.update_asset_description(asset_id, description)
        print(f"Updated description for {asset_id}: {result.get('id', 'OK')}")


if __name__ == "__main__":
    main()
