"""Command-line interface for FileBrowser transfers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from .agent_config import ConfigNotFoundError
from .config import ConfigError, load_skill_config
from .filebrowser import FileBrowserError, normalize_remote_path
from .models import ConfigurationError, SkillConfig
from .object_storage import CliObjectStorageGateway, ObjectStorageError, ObjectStorageGateway
from .transfer import TransferService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fb-transfer",
        description="Transfer files between FileBrowser and configured upload targets",
    )
    parser.add_argument("--config", help="Explicit agent_config.toml path")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Compatibility flag; this CLI never prompts",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate configuration without network access")
    doctor.add_argument("--json", action="store_true")

    listing = subparsers.add_parser("list", help="List configured sources and upload targets")
    listing.add_argument("--json", action="store_true")

    upload = subparsers.add_parser("upload", help="Upload one FileBrowser file")
    upload.add_argument("remote_path", help="Absolute FileBrowser file path")
    upload.add_argument("--source", help="Configured FileBrowser source name")
    upload.add_argument("--target", help="Configured upload target name")
    upload.add_argument("--key", help="Override the relative target object key")
    upload.add_argument("--overwrite", action="store_true")
    upload.add_argument(
        "--if-changed",
        action="store_true",
        help="With --overwrite, skip when the existing S3 object has the same SHA-256",
    )
    upload.add_argument("--dry-run", action="store_true")
    upload.add_argument("--json", action="store_true")

    get = subparsers.add_parser("get", help="Download one FileBrowser file to a local path")
    get.add_argument("remote_path", help="Absolute FileBrowser file path")
    get.add_argument("local_path", help="New local destination file path")
    get.add_argument("--source", help="Configured FileBrowser source name")
    get.add_argument("--dry-run", action="store_true")
    get.add_argument("--json", action="store_true")

    put = subparsers.add_parser("put", help="Upload one local file to FileBrowser")
    put.add_argument("local_path", help="Existing local file path")
    put.add_argument("remote_path", help="Absolute FileBrowser destination file path")
    put.add_argument("--source", help="Configured FileBrowser source name")
    put.add_argument("--overwrite", action="store_true")
    put.add_argument("--dry-run", action="store_true")
    put.add_argument("--json", action="store_true")

    cdn = subparsers.add_parser("cdn", help="Manage CDN cache (purge URL/path, prefetch)")
    cdn_sub = cdn.add_subparsers(dest="cdn_command", required=True)

    purge_url = cdn_sub.add_parser("purge-url", help="Purge cached CDN URLs")
    purge_url.add_argument("--target", help="Configured upload target name")
    purge_url_urls = purge_url.add_mutually_exclusive_group(required=True)
    purge_url_urls.add_argument("--urls", nargs="+", help="Full CDN URLs to purge")
    purge_url_urls.add_argument(
        "--keys", nargs="+", help="Object keys joined with the cdn base_url"
    )
    purge_url.add_argument("--dry-run", action="store_true")
    purge_url.add_argument("--json", action="store_true")

    purge_path = cdn_sub.add_parser("purge-path", help="Purge CDN cache under directories")
    purge_path.add_argument("--target", help="Configured upload target name")
    purge_path.add_argument("--paths", nargs="+", required=True, help="Directory paths to purge")
    purge_path.add_argument("--flush-type", choices=["flush", "delete"], default="flush")
    purge_path.add_argument("--dry-run", action="store_true")
    purge_path.add_argument("--json", action="store_true")

    prefetch = cdn_sub.add_parser("prefetch", help="Prefetch CDN URLs to edge nodes")
    prefetch.add_argument("--target", help="Configured upload target name")
    prefetch.add_argument("--urls", nargs="+", required=True, help="CDN URLs to prefetch")
    prefetch.add_argument("--area", choices=["mainland", "overseas"], default="")
    prefetch.add_argument("--dry-run", action="store_true")
    prefetch.add_argument("--json", action="store_true")

    # ----- File management commands -----

    info_p = subparsers.add_parser("info", help="Read FileBrowser resource metadata")
    info_p.add_argument("--source", help="Configured FileBrowser source name")
    info_p.add_argument("--path", required=True, help="Absolute FileBrowser path")
    info_p.add_argument(
        "--content", action="store_true", help="Inline small text content in the response"
    )
    info_p.add_argument("--json", action="store_true")

    update_p = subparsers.add_parser("update", help="Overwrite a FileBrowser file from stdin")
    update_p.add_argument("--source", help="Configured FileBrowser source name")
    update_p.add_argument("--path", required=True, help="Absolute FileBrowser file path")
    update_p.add_argument("--override", action="store_true")
    update_p.add_argument("--json", action="store_true")

    delete_p = subparsers.add_parser("delete", help="Delete a FileBrowser resource")
    delete_p.add_argument("--source", help="Configured FileBrowser source name")
    delete_p.add_argument(
        "--path", required=True, help="Absolute FileBrowser file or directory path"
    )
    delete_p.add_argument("--json", action="store_true")

    mkdir_p = subparsers.add_parser("mkdir", help="Create a FileBrowser directory")
    mkdir_p.add_argument("--source", help="Configured FileBrowser source name")
    mkdir_p.add_argument("--path", required=True, help="Absolute FileBrowser directory path")
    mkdir_p.add_argument("--json", action="store_true", help="Print result as JSON")

    move_p = subparsers.add_parser("move", help="Rename, move, or copy a resource")
    move_p.add_argument("--source", help="Configured FileBrowser source name")
    move_p.add_argument("--from", dest="from_path", required=True, help="Source path")
    move_p.add_argument("--destination", required=True, help="Destination path (absolute, no '..')")
    move_p.add_argument(
        "--action",
        choices=["rename", "copy"],
        default="rename",
        help="Move semantics (default: rename)",
    )
    move_p.add_argument("--overwrite", action="store_true")
    move_p.add_argument("--json", action="store_true")

    download_p = subparsers.add_parser(
        "download-files",
        help="Bundle multiple FileBrowser files into a single archive",
    )
    download_p.add_argument("--source", help="Configured FileBrowser source name")
    download_p.add_argument(
        "--files",
        required=True,
        help='FileBrowser paths joined by "||" (each "source::/abs/path")',
    )
    download_p.add_argument("--algo", choices=["zip", "tar.gz"], default="zip")
    download_p.add_argument("--output", help="Output archive path")
    download_p.add_argument("--json", action="store_true")

    preview_p = subparsers.add_parser("preview", help="Download a thumbnail preview")
    preview_p.add_argument("--source", help="Configured FileBrowser source name")
    preview_p.add_argument("--path", required=True, help="Absolute FileBrowser file path")
    preview_p.add_argument("--size", help="Preview size (provider-specific)")
    preview_p.add_argument("--output", help="Output preview path")
    preview_p.add_argument("--json", action="store_true")

    search_p = subparsers.add_parser("search", help="Search for files on a source")
    search_p.add_argument("--source", help="Configured FileBrowser source name")
    search_p.add_argument("--query", required=True, help="Search query")
    search_p.add_argument("--scope", help="Restrict search to a directory path")
    search_p.add_argument("--json", action="store_true")

    sources_p = subparsers.add_parser("sources", help="List configured FileBrowser sources")
    sources_p.add_argument("--source", help="Source to query (defaults to configured)")
    sources_p.add_argument("--json", action="store_true")

    list_dir_p = subparsers.add_parser("list-dir", help="List immediate children of a directory")
    list_dir_p.add_argument("--source", help="Configured FileBrowser source name")
    list_dir_p.add_argument("--path", required=True, help="Absolute FileBrowser directory path")
    list_dir_p.add_argument("--json", action="store_true")

    return parser


def _summary(
    config: SkillConfig,
    config_path: Path,
    object_storage: ObjectStorageGateway,
) -> dict[str, object]:
    sources: list[dict[str, object]] = []
    for source in config.sources.values():
        sources.append(
            {
                "name": source.name,
                "base_url": source.base_url,
                "source": source.source,
                "token_configured": source.token.configured,
            }
        )
    storage_summary = object_storage.summary()
    raw_targets = storage_summary.get("targets", [])
    targets: list[object] = cast(list[object], raw_targets) if isinstance(raw_targets, list) else []
    return {
        "config_path": str(config_path),
        "default_source": config.default_source,
        "default_target": storage_summary.get("default_target", ""),
        "sources": sources,
        "targets": targets,
        "object_storage_config_path": storage_summary.get("config_path", ""),
    }


def _print_payload(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _optional_string(args: argparse.Namespace, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) and value else None


def _run_cdn_command(
    args: argparse.Namespace,
    object_storage: ObjectStorageGateway,
    as_json: bool,
) -> int:
    cdn_command = cast(str, args.cdn_command)
    urls: list[str] | None = None
    keys: list[str] | None = None
    if cdn_command == "purge-url":
        urls = cast(list[str] | None, args.urls)
        keys = cast(list[str] | None, args.keys)
    elif cdn_command == "purge-path":
        urls = cast(list[str], args.paths)
    else:
        urls = cast(list[str], args.urls)
    payload = object_storage.cdn(
        cdn_command,
        target_name=_optional_string(args, "target"),
        urls=urls,
        keys=keys,
        flush_type=cast(str, getattr(args, "flush_type", "flush")),
        area=cast(str, getattr(args, "area", "")),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    _print_payload(payload, as_json=as_json)
    return 0 if payload.get("status", "submitted") == "submitted" else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config, config_path = load_skill_config(_optional_string(args, "config"))
        command = cast(str, args.command)
        as_json = bool(getattr(args, "json", False))
        if command in {"doctor", "list"}:
            object_storage = CliObjectStorageGateway(config_path)
            _print_payload(_summary(config, config_path, object_storage), as_json=as_json)
            return 0

        if command == "cdn":
            object_storage = CliObjectStorageGateway(config_path)
            return _run_cdn_command(args, object_storage, as_json)

        service = TransferService(config, config_path=config_path)
        if command == "get":
            remote_path = cast(str, args.remote_path)
            local_path = cast(str, args.local_path)
            source_name = _optional_string(args, "source")
            if bool(args.dry_run):
                plan = service.get_plan(remote_path, local_path, source_name=source_name)
                payload = cast(dict[str, object], asdict(plan))
                payload["dry_run"] = True
                _print_payload(payload, as_json=as_json)
                return 0
            result = service.get(remote_path, local_path, source_name=source_name)
            _print_payload(cast(dict[str, object], asdict(result)), as_json=as_json)
            return 0

        if command == "put":
            local_path = cast(str, args.local_path)
            remote_path = cast(str, args.remote_path)
            source_name = _optional_string(args, "source")
            if bool(args.dry_run):
                plan = service.put_plan(local_path, remote_path, source_name=source_name)
                payload = cast(dict[str, object], asdict(plan))
                payload["dry_run"] = True
                _print_payload(payload, as_json=as_json)
                return 0
            result = service.put(
                local_path,
                remote_path,
                source_name=source_name,
                overwrite=bool(args.overwrite),
            )
            _print_payload(cast(dict[str, object], asdict(result)), as_json=as_json)
            return 0

        if command == "info":
            payload = service.info(
                cast(str, args.path),
                source_name=_optional_string(args, "source"),
                content=bool(args.content),
            )
            _print_payload(payload, as_json=as_json)
            return 0

        if command == "update":
            data = sys.stdin.buffer.read()
            service.update_file(
                cast(str, args.path),
                data,
                source_name=_optional_string(args, "source"),
                overwrite=bool(args.override),
            )
            _print_payload(
                {"path": normalize_remote_path(cast(str, args.path)), "bytes": len(data)},
                as_json=as_json,
            )
            return 0

        if command == "delete":
            service.delete(cast(str, args.path), source_name=_optional_string(args, "source"))
            _print_payload({"path": normalize_remote_path(cast(str, args.path))}, as_json=as_json)
            return 0

        if command == "mkdir":
            created = service.ensure_dir(
                cast(str, args.path), source_name=_optional_string(args, "source")
            )
            _print_payload({"path": created}, as_json=as_json)
            return 0

        if command == "move":
            service.move(
                cast(str, args.from_path),
                cast(str, args.destination),
                action=cast(Literal["rename", "copy"], args.action),
                overwrite=bool(args.overwrite),
                source_name=_optional_string(args, "source"),
            )
            _print_payload(
                {
                    "from": normalize_remote_path(cast(str, args.from_path)),
                    "destination": normalize_remote_path(cast(str, args.destination)),
                    "action": args.action,
                },
                as_json=as_json,
            )
            return 0

        if command == "download-files":
            files = [item for item in cast(str, args.files).split("||") if item]
            destination = service.download_files(
                files,
                algo=cast(str, args.algo),
                output=_optional_string(args, "output"),
                source_name=_optional_string(args, "source"),
            )
            _print_payload(
                {"output": str(destination), "count": len(files), "algo": args.algo},
                as_json=as_json,
            )
            return 0

        if command == "preview":
            preview_path = service.preview(
                cast(str, args.path),
                size=_optional_string(args, "size"),
                output=_optional_string(args, "output"),
                source_name=_optional_string(args, "source"),
            )
            _print_payload({"output": str(preview_path)}, as_json=as_json)
            return 0

        if command == "search":
            results = service.search(
                cast(str, args.query),
                scope=_optional_string(args, "scope"),
                source_name=_optional_string(args, "source"),
            )
            _print_payload({"count": len(results), "results": results}, as_json=as_json)
            return 0

        if command == "sources":
            items = service.list_sources(source_name=_optional_string(args, "source"))
            _print_payload({"count": len(items), "sources": items}, as_json=as_json)
            return 0

        if command == "list-dir":
            items = service.list_files(
                cast(str, args.path), source_name=_optional_string(args, "source")
            )
            _print_payload(
                {
                    "path": cast(str, args.path),
                    "count": len(items),
                    "items": items,
                },
                as_json=as_json,
            )
            return 0

        remote_path = cast(str, args.remote_path)
        source_name = _optional_string(args, "source")
        target_name = _optional_string(args, "target")
        object_key = _optional_string(args, "key")
        if bool(args.if_changed) and not bool(args.overwrite):
            raise FileBrowserError("--if-changed requires --overwrite")
        if bool(args.dry_run):
            plan = service.plan(
                remote_path,
                source_name=source_name,
                target_name=target_name,
                object_key=object_key,
            )
            payload = cast(dict[str, object], asdict(plan))
            payload["dry_run"] = True
            payload["if_changed"] = bool(args.if_changed)
            _print_payload(payload, as_json=as_json)
            return 0

        result = service.upload(
            remote_path,
            source_name=source_name,
            target_name=target_name,
            object_key=object_key,
            overwrite=bool(args.overwrite),
            if_changed=bool(args.if_changed),
        )
        _print_payload(cast(dict[str, object], asdict(result)), as_json=as_json)
        return 0
    except (
        ConfigNotFoundError,
        ConfigError,
        ConfigurationError,
        FileBrowserError,
        ObjectStorageError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
