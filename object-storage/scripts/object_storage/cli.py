"""object-storage 命令行接口。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from .agent_config import ConfigNotFoundError
from .config import ConfigError, load_skill_config
from .models import CdnTaskResult, ConfigurationError, ObjectStorageConfig, S3TargetConfig
from .service import ObjectStorageService
from .target import TargetError, normalize_object_key, resolve_target_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="object-storage",
        description="Upload local files to S3-compatible storage and manage CDN caches",
    )
    parser.add_argument("--config", help="Explicit agent_config.toml path")
    parser.add_argument("--non-interactive", action="store_true", help="CLI never prompts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command, help_text in (
        ("doctor", "Validate configuration without network access"),
        ("list", "List configured object storage targets"),
    ):
        item = subparsers.add_parser(command, help=help_text)
        item.add_argument("--json", action="store_true")

    resolve = subparsers.add_parser("resolve-key", help="Resolve a relative object key")
    resolve.add_argument("key")
    resolve.add_argument("--target")
    resolve.add_argument("--json", action="store_true")

    upload = subparsers.add_parser("upload", help="Upload one local file")
    upload.add_argument("local_path")
    upload.add_argument("--target")
    upload.add_argument("--key", help="Relative object key; defaults to the file name")
    upload.add_argument("--content-type", default="")
    upload.add_argument("--overwrite", action="store_true")
    upload.add_argument(
        "--if-changed",
        action="store_true",
        help="With --overwrite, skip when size and stored SHA-256 are unchanged",
    )
    upload.add_argument("--dry-run", action="store_true")
    upload.add_argument("--json", action="store_true")

    cdn = subparsers.add_parser("cdn", help="Manage CDN cache")
    cdn_sub = cdn.add_subparsers(dest="cdn_command", required=True)
    purge_url = cdn_sub.add_parser("purge-url")
    purge_url.add_argument("--target")
    purge_url_group = purge_url.add_mutually_exclusive_group(required=True)
    purge_url_group.add_argument("--urls", nargs="+")
    purge_url_group.add_argument("--keys", nargs="+")
    purge_url.add_argument("--dry-run", action="store_true")
    purge_url.add_argument("--json", action="store_true")

    purge_path = cdn_sub.add_parser("purge-path")
    purge_path.add_argument("--target")
    purge_path_group = purge_path.add_mutually_exclusive_group(required=True)
    purge_path_group.add_argument("--paths", nargs="+")
    purge_path_group.add_argument("--keys", nargs="+")
    purge_path.add_argument("--flush-type", choices=["flush", "delete"], default="flush")
    purge_path.add_argument("--dry-run", action="store_true")
    purge_path.add_argument("--json", action="store_true")

    prefetch = cdn_sub.add_parser("prefetch")
    prefetch.add_argument("--target")
    prefetch_group = prefetch.add_mutually_exclusive_group(required=True)
    prefetch_group.add_argument("--urls", nargs="+")
    prefetch_group.add_argument("--keys", nargs="+")
    prefetch.add_argument("--area", choices=["mainland", "overseas"], default="")
    prefetch.add_argument("--dry-run", action="store_true")
    prefetch.add_argument("--json", action="store_true")
    return parser


def _optional_string(args: argparse.Namespace, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) and value else None


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _credential_mode(target: S3TargetConfig) -> str:
    if target.profile:
        return "profile"
    if target.access_key_id.direct:
        return "config"
    if target.access_key_id.env_var:
        return "environment"
    return "default-chain"


def _summary(config: ObjectStorageConfig, config_path: Path) -> dict[str, object]:
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
                "cdn_purge_on_upload": target.cdn.purge_on_upload if target.cdn else False,
            }
        )
    return {
        "config_path": str(config_path),
        "default_target": config.default_target,
        "targets": targets,
    }


def _cdn_targets(args: argparse.Namespace, service: ObjectStorageService) -> list[str]:
    urls = cast(list[str] | None, getattr(args, "urls", None))
    keys = cast(list[str] | None, getattr(args, "keys", None))
    if urls:
        return list(urls)
    manager = service.cdn_manager(_optional_string(args, "target"))
    return [manager.build_url(normalize_object_key(key)) for key in keys or []]


def _run_cdn(args: argparse.Namespace, service: ObjectStorageService, as_json: bool) -> int:
    command = cast(str, args.cdn_command)
    manager = service.cdn_manager(_optional_string(args, "target"))
    targets = _cdn_targets(args, service)
    operation = {"purge-url": "purge_url", "purge-path": "purge_path", "prefetch": "prefetch"}[
        command
    ]
    if bool(args.dry_run):
        if command == "purge-path":
            targets = [target if target.endswith("/") else f"{target}/" for target in targets]
        _print({"operation": operation, "dry_run": True, "targets": targets}, as_json=as_json)
        return 0
    task: CdnTaskResult
    if command == "purge-url":
        task = manager.purge_url(targets)
    elif command == "purge-path":
        task = manager.purge_path(
            targets, flush_type=cast(Literal["flush", "delete"], args.flush_type)
        )
    else:
        task = manager.prefetch(targets, area=cast(str, args.area))
    _print(cast(dict[str, object], asdict(task)), as_json=as_json)
    return 0 if task.status == "submitted" else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config, config_path = load_skill_config(_optional_string(args, "config"))
        command = cast(str, args.command)
        as_json = bool(getattr(args, "json", False))
        if command in {"doctor", "list"}:
            _print(_summary(config, config_path), as_json=as_json)
            return 0
        if command == "resolve-key":
            target = config.target(_optional_string(args, "target"))
            key = resolve_target_key(target, cast(str, args.key))
            _print(
                {"target_name": target.name, "object_key": key},
                as_json=as_json,
            )
            return 0
        service = ObjectStorageService(config)
        if command == "cdn":
            return _run_cdn(args, service, as_json)
        overwrite = bool(args.overwrite)
        if_changed = bool(args.if_changed)
        if if_changed and not overwrite:
            raise ValueError("--if-changed requires --overwrite")
        if bool(args.dry_run):
            plan = service.plan(
                cast(str, args.local_path),
                target_name=_optional_string(args, "target"),
                object_key=_optional_string(args, "key"),
                overwrite=overwrite,
                if_changed=if_changed,
            )
            payload = cast(dict[str, object], asdict(plan))
            payload["dry_run"] = True
            _print(payload, as_json=as_json)
            return 0
        result = service.upload(
            cast(str, args.local_path),
            target_name=_optional_string(args, "target"),
            object_key=_optional_string(args, "key"),
            overwrite=overwrite,
            if_changed=if_changed,
            content_type=cast(str, args.content_type),
        )
        _print(cast(dict[str, object], asdict(result)), as_json=as_json)
        return 0
    except (
        ConfigNotFoundError,
        ConfigError,
        ConfigurationError,
        TargetError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
