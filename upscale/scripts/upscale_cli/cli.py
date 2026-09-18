from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import load_settings
from .errors import ConfigurationError, ServiceUnavailableError, UpscaleError
from .service import UpscaleService, run_filebrowser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config")


def _task_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--media-type", choices=("image", "video"))
    parser.add_argument("--model")
    parser.add_argument(
        "--mode", choices=("upscale", "enhance", "resize"), default="upscale"
    )
    parser.add_argument("--scale", type=float)
    parser.add_argument("--target-width", type=int)
    parser.add_argument("--target-height", type=int)
    parser.add_argument("--fit", choices=("contain", "cover"), default="contain")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--target-fps")
    parser.add_argument(
        "--interpolation-model", choices=("rife-v4.25", "rife-v4.25-lite")
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict upscale-api client")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Accepted for automation; the CLI never prompts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser(
        "status", help="Read live health, queue, GPU, and capabilities"
    )
    _common(status)
    status.set_defaults(func=_status)

    models = sub.add_parser("models", help="List live models and submission defaults")
    _common(models)
    models.set_defaults(func=_models)

    cancel = sub.add_parser("cancel", help="Cancel a queued or running task")
    _common(cancel)
    cancel.add_argument("task_id", help="upscale-api task ID")
    cancel.add_argument(
        "--wait", action="store_true", help="Wait until the task reaches a terminal state"
    )
    cancel.set_defaults(func=_cancel)

    run = sub.add_parser("run", help="Upscale a local image or video")
    _common(run)
    _task_options(run)
    run.add_argument("--input", required=True)
    run.add_argument("--output")
    run.set_defaults(func=_run)

    remote = sub.add_parser(
        "filebrowser", help="Upscale a FileBrowser file and return it"
    )
    _common(remote)
    _task_options(remote)
    remote.add_argument("--path", required=True, help="FileBrowser absolute input path")
    remote.add_argument("--source", help="Configured FileBrowser source name")
    remote.add_argument("--output", help="FileBrowser absolute output path")
    remote.add_argument("--local-output", help="Optional retained local result")
    remote.set_defaults(func=_filebrowser)
    return parser


def _service(args: argparse.Namespace) -> UpscaleService:
    return UpscaleService(load_settings(args.config))


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _status(args: argparse.Namespace) -> None:
    _print(_service(args).probe())


def _models(args: argparse.Namespace) -> None:
    live = _service(args).probe()
    capabilities = live["capabilities"]
    _print(
        {
            "available": True,
            "config_path": live["config_path"],
            "base_url": live["base_url"],
            "service_version": live["health"].get("version"),
            "models": capabilities.get("models", {}),
            "submission_defaults": capabilities.get("submission_defaults", {}),
            "selection_profiles": capabilities.get("selection_profiles", {}),
        }
    )


def _cancel(args: argparse.Namespace) -> None:
    _print(_service(args).cancel(args.task_id, wait=args.wait))


def _run(args: argparse.Namespace) -> None:
    _print(
        _service(args).run(
            args.input,
            output_path=args.output,
            media_type=args.media_type,
            model=args.model,
            mode=args.mode,
            scale=args.scale,
            target_width=args.target_width,
            target_height=args.target_height,
            fit=args.fit,
            start=args.start,
            duration=args.duration,
            target_fps=args.target_fps,
            interpolation_model=args.interpolation_model,
            force=args.force,
            dry_run=args.dry_run,
        )
    )


def _filebrowser(args: argparse.Namespace) -> None:
    service = _service(args)
    _print(
        run_filebrowser(
            service,
            args.path,
            source_name=args.source,
            remote_output=args.output,
            local_output=args.local_output,
            media_type=args.media_type,
            model=args.model,
            mode=args.mode,
            scale=args.scale,
            target_width=args.target_width,
            target_height=args.target_height,
            fit=args.fit,
            start=args.start,
            duration=args.duration,
            target_fps=args.target_fps,
            interpolation_model=args.interpolation_model,
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
    except UpscaleError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
