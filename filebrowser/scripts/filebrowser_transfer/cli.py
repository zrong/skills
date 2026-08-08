"""Command-line interface for FileBrowser transfers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from .agent_config import ConfigNotFoundError
from .cdn import build_cdn_cache_manager
from .config import ConfigError, load_skill_config
from .filebrowser import FileBrowserError, normalize_remote_path
from .models import CdnTaskResult, ConfigurationError, S3TargetConfig, SkillConfig
from .targets import TargetError
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
    download_p.add_argument("--algo", choices=["zip", "tar", "tar.gz"], default="zip")
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


def _credential_mode(target: S3TargetConfig) -> str:
    if target.profile:
        return "profile"
    if target.access_key_id.direct or target.secret_access_key.direct:
        return "config"
    if target.access_key_id.env_var or target.secret_access_key.env_var:
        return "environment"
    return "default-chain"


def _summary(config: SkillConfig, config_path: Path) -> dict[str, object]:
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
    targets: list[dict[str, object]] = []
    for target in config.targets.values():
        targets.append(
            {
                "name": target.name,
                "adapter": "s3",
                "bucket": target.bucket,
                "region": target.region,
                "endpoint_url": target.endpoint_url,
                "prefix": target.prefix,
                "credential_mode": _credential_mode(target),
                "cdn_provider": target.cdn.provider if target.cdn else "",
                "cdn_purge_on_upload": bool(target.cdn.purge_on_upload) if target.cdn else False,
            }
        )
    return {
        "config_path": str(config_path),
        "default_source": config.default_source,
        "default_target": config.default_target,
        "sources": sources,
        "targets": targets,
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


def _print_cdn_dry_run(operation: str, targets: list[str], as_json: bool) -> int:
    payload: dict[str, object] = {
        "operation": operation,
        "dry_run": True,
        "targets": targets,
    }
    _print_payload(payload, as_json=as_json)
    return 0


def _print_cdn_task(task: CdnTaskResult, as_json: bool) -> int:
    _print_payload(cast(dict[str, object], asdict(task)), as_json=as_json)
    if not as_json:
        print(
            f"{task.operation} 已提交。TaskId: {task.task_id or '-'}。"
            "CDN 约 5 分钟内生效。请稍后自行访问测试。"
        )
    return 0 if task.status == "submitted" else 1


def _run_cdn_command(
    args: argparse.Namespace,
    config: SkillConfig,
    as_json: bool,
) -> int:
    cdn_command = cast(str, args.cdn_command)
    target_config = config.target(_optional_string(args, "target"))
    if target_config.cdn is None:
        print(
            f"ERROR: target {target_config.name} has no [cdn] subtable",
            file=sys.stderr,
        )
        return 1
    manager = build_cdn_cache_manager(target_config)
    if manager is None:
        print(
            f"ERROR: target {target_config.name} has no CDN configuration",
            file=sys.stderr,
        )
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    if cdn_command == "purge-url":
        raw_urls = cast(list[str] | None, args.urls)
        raw_keys = cast(list[str] | None, args.keys)
        targets = (
            list(raw_urls) if raw_urls else [manager.build_url(key) for key in (raw_keys or [])]
        )
        if dry_run:
            return _print_cdn_dry_run("purge_url", targets, as_json)
        task = manager.purge_url(targets)
    elif cdn_command == "purge-path":
        paths = cast(list[str], args.paths)
        targets = [path if path.endswith("/") else f"{path}/" for path in paths]
        if dry_run:
            return _print_cdn_dry_run("purge_path", targets, as_json)
        task = manager.purge_path(
            targets, flush_type=cast(Literal["flush", "delete"], args.flush_type)
        )
    else:  # prefetch
        targets = cast(list[str], args.urls)
        area = cast(str, args.area)
        if dry_run:
            return _print_cdn_dry_run("prefetch", targets, as_json)
        task = manager.prefetch(targets, area=area)
    return _print_cdn_task(task, as_json)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config, config_path = load_skill_config(_optional_string(args, "config"))
        command = cast(str, args.command)
        as_json = bool(getattr(args, "json", False))
        if command in {"doctor", "list"}:
            _print_payload(_summary(config, config_path), as_json=as_json)
            return 0

        if command == "cdn":
            return _run_cdn_command(args, config, as_json)

        service = TransferService(config)
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
        if bool(args.dry_run):
            plan = service.plan(
                remote_path,
                source_name=source_name,
                target_name=target_name,
                object_key=object_key,
            )
            payload = cast(dict[str, object], asdict(plan))
            payload["dry_run"] = True
            _print_payload(payload, as_json=as_json)
            return 0

        result = service.upload(
            remote_path,
            source_name=source_name,
            target_name=target_name,
            object_key=object_key,
            overwrite=bool(args.overwrite),
        )
        _print_payload(cast(dict[str, object], asdict(result)), as_json=as_json)
        return 0
    except (
        ConfigNotFoundError,
        ConfigError,
        ConfigurationError,
        FileBrowserError,
        TargetError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
