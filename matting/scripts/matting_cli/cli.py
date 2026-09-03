"""Non-interactive CLI for matting-api."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import load_settings
from .errors import ConfigurationError, MattingError, ServiceUnavailableError
from .selection import advertised
from .service import MattingService


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accepted for automation; the CLI never prompts",
    )


def _selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True)
    parser.add_argument("--method")
    parser.add_argument("--model")
    parser.add_argument("--parameters-json")
    parser.add_argument("--parameters-file")
    parser.add_argument("--reprocess", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict matting-api client")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Read live service status and capabilities")
    _common(status)
    status.set_defaults(func=_status)

    algorithms = sub.add_parser(
        "algorithms", help="List live methods, models, and proven compatible pairs"
    )
    _common(algorithms)
    algorithms.set_defaults(func=_algorithms)

    inspect = sub.add_parser(
        "inspect", help="Inspect an image and recommend a live-compatible method/model"
    )
    _common(inspect)
    _selection(inspect)
    inspect.set_defaults(func=_inspect)

    remove = sub.add_parser(
        "remove", help="Remove background and write a transparent PNG"
    )
    _common(remove)
    _selection(remove)
    remove.add_argument("--out", required=True)
    remove.add_argument("--force", action="store_true")
    remove.add_argument("--dry-run", action="store_true")
    remove.set_defaults(func=_remove)
    return parser


def _service(args: argparse.Namespace) -> MattingService:
    return MattingService(load_settings(args.config))


def _parameters(args: argparse.Namespace) -> dict[str, Any]:
    if args.parameters_json and args.parameters_file:
        raise MattingError("--parameters-json 与 --parameters-file 不能同时使用")
    raw = args.parameters_json
    if args.parameters_file:
        path = Path(args.parameters_file).expanduser()
        if not path.is_file():
            raise MattingError(f"参数文件不存在: {path}")
        raw = path.read_text(encoding="utf-8")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MattingError(f"参数 JSON 无效: {exc}") from exc
    if not isinstance(value, dict):
        raise MattingError("参数必须是 JSON 对象")
    return value


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _status(args: argparse.Namespace) -> None:
    _print(_service(args).probe())


def _algorithms(args: argparse.Namespace) -> None:
    live = _service(args).probe()
    methods, models = advertised(live["capabilities"])
    _print(
        {
            "available": True,
            "config_path": live["config_path"],
            "base_url": live["base_url"],
            "methods": sorted(methods),
            "models": sorted(models),
            "compatible_pairs": live["compatible_pairs"],
        }
    )


def _inspect(args: argparse.Namespace) -> None:
    _print(
        _service(args).plan(
            args.input,
            method=args.method,
            model=args.model,
            parameters=_parameters(args),
            reprocess=args.reprocess,
        )
    )


def _remove(args: argparse.Namespace) -> None:
    _print(
        _service(args).remove(
            args.input,
            args.out,
            method=args.method,
            model=args.model,
            parameters=_parameters(args),
            reprocess=args.reprocess,
            force=args.force,
            dry_run=args.dry_run,
        )
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
        return 0
    except ConfigurationError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except ServiceUnavailableError as exc:
        print(f"服务不可用: {exc}", file=sys.stderr)
        return 3
    except MattingError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
