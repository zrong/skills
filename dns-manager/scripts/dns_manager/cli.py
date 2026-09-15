"""dns-manager CLI: non-interactive, JSON-first DNS record management."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .config import ConfigError, load_skill_config
from .providers import DnsProviderError, get_provider

SKILL_DIR = Path(__file__).resolve().parents[2]


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            print(f"{key}:")
            for item in value:
                print("  " + json.dumps(item, ensure_ascii=False))
        else:
            print(f"{key}: {value}")


def _build_provider(args: argparse.Namespace):
    config = load_skill_config(
        SKILL_DIR,
        path=getattr(args, "config", None),
    )
    return get_provider(config.provider, config.provider_settings), config


def _as_dict(obj) -> dict:
    return dataclasses.asdict(obj)


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        config = load_skill_config(SKILL_DIR, path=args.config)
    except ConfigError as exc:
        _emit({"ok": False, "error": str(exc)}, args.json)
        return 1
    provider = get_provider(config.provider, config.provider_settings)
    _emit(
        {
            "ok": True,
            "provider": config.provider,
            "config_path": str(config.config_path) if config.config_path else None,
            "credentials_resolved": config.resolved,
            "provider_class": type(provider).__name__,
        },
        args.json,
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    provider, config = _build_provider(args)
    records = provider.list_records(
        args.domain, subdomain=args.subdomain, record_type=args.type
    )
    _emit(
        {
            "ok": True,
            "provider": config.provider,
            "domain": args.domain,
            "count": len(records),
            "records": [_as_dict(r) for r in records],
        },
        args.json,
    )
    return 0


def cmd_upsert(args: argparse.Namespace) -> int:
    provider, config = _build_provider(args)
    if args.dry_run:
        _emit(
            {
                "ok": True,
                "dry_run": True,
                "provider": config.provider,
                "would_upsert": {
                    "domain": args.domain,
                    "subdomain": args.subdomain,
                    "type": args.type.upper(),
                    "value": args.value,
                    "ttl": args.ttl,
                    "line": args.line,
                },
            },
            args.json,
        )
        return 0
    result = provider.upsert_record(
        args.domain,
        args.subdomain,
        args.type,
        args.value,
        ttl=args.ttl,
        line=args.line,
    )
    _emit(
        {
            "ok": True,
            "action": result.action,
            "record": _as_dict(result),
        },
        args.json,
    )
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    provider, _config = _build_provider(args)
    if args.dry_run:
        existing = provider.list_records(
            args.domain, subdomain=args.subdomain, record_type=args.type
        )
        targets = [r for r in existing if args.value is None or r.value == args.value]
        _emit(
            {
                "ok": True,
                "dry_run": True,
                "would_delete": [_as_dict(r) for r in targets],
                "count": len(targets),
            },
            args.json,
        )
        return 0
    result = provider.delete_record(
        args.domain, args.subdomain, args.type, value=args.value
    )
    _emit({"ok": True, "action": result.action, "result": _as_dict(result)}, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dns-manager", description=__doc__)
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="explicit non-interactive mode (default; the CLI never prompts)",
    )
    parser.add_argument("--config", help="explicit agent_config.toml path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    # --json is also accepted after the subcommand for ergonomics; the
    # subparser copy must not clobber the root flag's value when absent.
    json_flag = argparse.ArgumentParser(add_help=False)
    json_flag.add_argument("--json", dest="sub_json", action="store_true")

    doctor = sub.add_parser(
        "doctor", help="validate config without network calls", parents=[json_flag]
    )
    doctor.set_defaults(func=cmd_doctor)

    lst = sub.add_parser("list", help="list records of a domain", parents=[json_flag])
    lst.add_argument("domain")
    lst.add_argument("--subdomain")
    lst.add_argument("--type")
    lst.set_defaults(func=cmd_list)

    upsert = sub.add_parser(
        "upsert", help="idempotent create-or-update a record", parents=[json_flag]
    )
    upsert.add_argument("domain")
    upsert.add_argument("--subdomain", required=True)
    upsert.add_argument("--type", required=True, help="A, CNAME, TXT, MX, ...")
    upsert.add_argument("--value", required=True)
    upsert.add_argument("--ttl", type=int, default=600)
    upsert.add_argument("--line", default=None, help='record line, default "默认"')
    upsert.add_argument("--dry-run", action="store_true")
    upsert.set_defaults(func=cmd_upsert)

    delete = sub.add_parser(
        "delete", help="idempotently delete matching records", parents=[json_flag]
    )
    delete.add_argument("domain")
    delete.add_argument("--subdomain", required=True)
    delete.add_argument("--type", required=True)
    delete.add_argument("--value", help="only delete records with this value")
    delete.add_argument("--dry-run", action="store_true")
    delete.set_defaults(func=cmd_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "sub_json", False):
        args.json = True
    try:
        return args.func(args)
    except (ConfigError, DnsProviderError) as exc:
        _emit({"ok": False, "error": str(exc)}, args.json)
        return 1


if __name__ == "__main__":
    sys.exit(main())
