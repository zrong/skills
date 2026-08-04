"""Command-line interface for FileBrowser transfers."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import cast

from .agent_config import ConfigNotFoundError
from .config import ConfigError, load_skill_config
from .filebrowser import FileBrowserError
from .models import ConfigurationError, S3TargetConfig, SkillConfig
from .targets import TargetError
from .transfer import TransferService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fb-transfer",
        description="Transfer FileBrowser files to configured upload targets",
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config, config_path = load_skill_config(_optional_string(args, "config"))
        command = cast(str, args.command)
        as_json = bool(getattr(args, "json", False))
        if command in {"doctor", "list"}:
            _print_payload(_summary(config, config_path), as_json=as_json)
            return 0

        service = TransferService(config)
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
