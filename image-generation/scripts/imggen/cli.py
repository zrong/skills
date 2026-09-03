"""Unified non-interactive CLI for OpenAI, Gemini, and Seedream image adapters."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from imggen.adapters import create_adapter
from imggen.chroma import remove_chroma_key
from imggen.config import get_endpoint_config, list_providers
from imggen.interactive import (
    Markup,
    SessionStore,
    marked_prompt,
    normalize_markup,
    parse_bbox,
    parse_canvas_size,
    parse_point,
)
from imggen.matting_bridge import remove_background
from imggen.models import CapabilityError, ImageRequest, ImggenError
from imggen.output import output_paths, preflight_outputs, save_artifacts
from imggen.prompting import PROMPT_FIELDS, augment_prompt, read_prompt
from imggen.service import execute, validate_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strict multi-provider AI image generation and editing CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser(
        "list", help="List configured providers, endpoints, and allowed models"
    )
    list_parser.add_argument("--config")
    list_parser.set_defaults(func=_list)

    models_parser = sub.add_parser(
        "models", help="Compare remote models with the local allowlist"
    )
    _add_endpoint_args(models_parser)
    models_parser.add_argument("--show-unconfigured", action="store_true")
    models_parser.set_defaults(func=_models)

    generate = sub.add_parser("generate", help="Generate a new image")
    _add_request_args(generate)
    generate.set_defaults(func=_run_single, operation="generate")

    edit = sub.add_parser("edit", help="Edit one or more reference images")
    _add_request_args(edit)
    edit.add_argument(
        "--image",
        action="append",
        required=True,
        help="Reference image; repeat for multiple",
    )
    edit.add_argument("--mask", help="OpenAI-compatible alpha mask")
    edit.set_defaults(func=_run_single, operation="edit")

    batch = sub.add_parser("generate-batch", help="Generate JSONL jobs concurrently")
    _add_request_args(batch, prompt_required=False)
    batch.add_argument(
        "--input", required=True, help="JSONL file; each row requires prompt"
    )
    batch.add_argument("--concurrency", type=int, default=3)
    batch.add_argument("--fail-fast", action="store_true")
    batch.set_defaults(func=_run_batch, operation="generate")

    interactive = sub.add_parser(
        "interactive", help="Seedream 5.0 Pro coordinate editing sessions"
    )
    isub = interactive.add_subparsers(dest="interactive_command", required=True)
    start = isub.add_parser("start", help="Create a session and perform its first edit")
    _add_interactive_args(start)
    start.add_argument("--image", required=True, help="Initial reference image")
    start.set_defaults(func=_interactive_start)
    turn = isub.add_parser("edit", help="Edit the latest completed session output")
    _add_interactive_args(turn, endpoint_optional=True)
    turn.set_defaults(func=_interactive_edit)
    retry = isub.add_parser("retry", help="Retry the last pending or failed turn")
    retry.add_argument("--session", required=True)
    retry.add_argument("--config")
    retry.add_argument("--out", "--output", "-o", dest="output")
    retry.add_argument("--out-dir")
    retry.add_argument("--force", action="store_true")
    retry.add_argument("--max-attempts", type=int, default=3)
    retry.set_defaults(func=_interactive_retry)
    show = isub.add_parser("show", help="Show session state as JSON")
    show.add_argument("--session", required=True)
    show.set_defaults(func=_interactive_show)

    chroma = sub.add_parser(
        "chroma-key", help="Convert a solid key-color background to alpha"
    )
    chroma.add_argument("--input", required=True)
    chroma.add_argument("--out", required=True)
    chroma.add_argument("--key-color", default="#00ff00")
    chroma.add_argument("--tolerance", type=int, default=12)
    chroma.add_argument(
        "--auto-key", choices=["none", "corners", "border"], default="none"
    )
    chroma.add_argument("--soft-matte", action="store_true")
    chroma.add_argument("--transparent-threshold", type=float, default=12.0)
    chroma.add_argument("--opaque-threshold", type=float, default=96.0)
    chroma.add_argument("--edge-feather", type=float, default=0.0)
    chroma.add_argument("--edge-contract", type=int, default=0)
    chroma.add_argument(
        "--spill-cleanup", "--despill", dest="spill_cleanup", action="store_true"
    )
    chroma.add_argument("--force", action="store_true")
    chroma.set_defaults(func=_chroma_key)

    remove = sub.add_parser(
        "remove-background",
        help="Use configured matting-api, or fall back to the existing chroma-key",
    )
    remove.add_argument("--input", required=True)
    remove.add_argument("--out", required=True)
    remove.add_argument(
        "--config", help="Shared agent_config.toml containing [matting]"
    )
    remove.add_argument("--method")
    remove.add_argument("--model")
    remove.add_argument("--parameters-json")
    remove.add_argument("--reprocess", action="store_true")
    remove.add_argument("--no-matting", action="store_true")
    remove.add_argument("--no-fallback", action="store_true")
    remove.add_argument("--fallback-key-color", default="#00ff00")
    remove.add_argument(
        "--fallback-auto-key", choices=["none", "corners", "border"], default="border"
    )
    remove.add_argument("--fallback-tolerance", type=int, default=12)
    remove.add_argument("--fallback-transparent-threshold", type=float, default=12.0)
    remove.add_argument("--fallback-opaque-threshold", type=float, default=96.0)
    remove.add_argument("--fallback-edge-feather", type=float, default=0.0)
    remove.add_argument("--fallback-edge-contract", type=int, default=0)
    remove.add_argument("--force", action="store_true")
    remove.add_argument("--dry-run", action="store_true")
    remove.set_defaults(func=_remove_background)
    return parser


def _add_endpoint_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-p", "--provider")
    parser.add_argument("-e", "--endpoint")
    parser.add_argument("-m", "--model")
    parser.add_argument("--config")


def _add_request_args(
    parser: argparse.ArgumentParser, prompt_required: bool = True
) -> None:
    _add_endpoint_args(parser)
    parser.add_argument("prompt_pos", nargs="?", help="Prompt (legacy positional form)")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("-n", type=int, default=1)
    parser.add_argument("-s", "--size")
    parser.add_argument("--quality")
    parser.add_argument("--background")
    parser.add_argument("--output-format", choices=["png", "jpeg", "jpg", "webp"])
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation")
    parser.add_argument("--input-fidelity")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--stream", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument(
        "--watermark", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument("--sequential", choices=["auto", "disabled"])
    parser.add_argument("--aspect-ratio")
    parser.add_argument("--image-size")
    parser.add_argument("--out", "--output", "-o", dest="output")
    parser.add_argument("--out-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--augment", action=argparse.BooleanOptionalAction, default=True
    )
    for field in PROMPT_FIELDS:
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field)
    parser.add_argument("--downscale-max-dim", type=int)
    parser.add_argument("--downscale-suffix", default="-small")
    parser.set_defaults(prompt_required=prompt_required)


def _add_interactive_args(
    parser: argparse.ArgumentParser, endpoint_optional: bool = False
) -> None:
    _add_request_args(parser)
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--point",
        action="append",
        default=[],
        help="X,Y; normalized unless --canvas-size",
    )
    parser.add_argument(
        "--bbox",
        action="append",
        default=[],
        help="X1,Y1,X2,Y2; normalized unless --canvas-size",
    )
    parser.add_argument(
        "--canvas-size", help="Source pixel coordinate space, WIDTHxHEIGHT"
    )
    parser.set_defaults(endpoint_optional=endpoint_optional)


def _list(args: argparse.Namespace) -> None:
    for provider in list_providers(config_path=args.config):
        default = " (default)" if provider["is_default"] else ""
        print(f"{provider['key']}: {provider['name']}{default}")
        for endpoint in provider["endpoints"]:
            print(
                f"  {endpoint['key']}: adapter={endpoint['adapter']} base_url={endpoint['base_url']} "
                f"default_model={endpoint['default_model'] or '-'}"
            )
            for model in endpoint["models"]:
                print(f"    - {model}")


def _models(args: argparse.Namespace) -> None:
    endpoint = get_endpoint_config(
        args.provider, args.endpoint, config_path=args.config
    )
    remote = set(create_adapter(endpoint).list_models())
    configured = set(endpoint.models)
    print(
        f"provider={endpoint.provider_key} endpoint={endpoint.endpoint_key} adapter={endpoint.adapter}"
    )
    print("configured and remotely visible:")
    for name in sorted(configured & remote):
        print(f"  {name}")
    for name in sorted(configured - remote):
        print(f"  {name} (configured; not returned by remote /models)")
    unconfigured = sorted(remote - configured)
    print(f"remote but blocked by allowlist: {len(unconfigured)}")
    if args.show_unconfigured:
        for name in unconfigured:
            print(f"  {name}")


def _run_single(args: argparse.Namespace) -> None:
    endpoint, request = _request_from_args(args, args.operation)
    if args.dry_run:
        validate_request(request)
        _print_dry_run(endpoint, request, args.output, args.out_dir)
        return
    preflight_outputs(
        request.n,
        args.output,
        args.out_dir,
        request.output_format,
        args.force,
        args.downscale_max_dim,
        args.downscale_suffix,
    )
    artifacts = execute(endpoint, request, args.max_attempts)
    paths = save_artifacts(
        artifacts,
        args.output,
        args.out_dir,
        request.output_format,
        args.force,
        args.downscale_max_dim,
        args.downscale_suffix,
    )
    for path in paths:
        print(path)


def _request_from_args(
    args: argparse.Namespace,
    operation: str,
    overrides: dict[str, Any] | None = None,
) -> tuple[Any, ImageRequest]:
    values = vars(args).copy()
    values.update(
        {key: value for key, value in (overrides or {}).items() if value is not None}
    )
    endpoint = get_endpoint_config(
        values.get("provider"), values.get("endpoint"), config_path=values.get("config")
    )
    policy = endpoint.resolve_model(
        values.get("model"), operation
    )  # exact allowlist check before network
    prompt = read_prompt(
        values.get("prompt_pos"), values.get("prompt"), values.get("prompt_file")
    )
    fields = {field: values.get(field) for field in PROMPT_FIELDS}
    prompt = augment_prompt(prompt, fields, bool(values.get("augment", True)))
    references = [Path(value).expanduser() for value in values.get("image", [])]
    request = ImageRequest(
        operation=operation,
        prompt=prompt,
        model=policy,
        references=references,
        mask=Path(values["mask"]).expanduser() if values.get("mask") else None,
        n=int(values.get("n", 1)),
        size=values.get("size"),
        quality=values.get("quality"),
        background=values.get("background"),
        output_format=values.get("output_format"),
        output_compression=values.get("output_compression"),
        moderation=values.get("moderation"),
        input_fidelity=values.get("input_fidelity"),
        seed=values.get("seed"),
        stream=values.get("stream"),
        watermark=values.get("watermark"),
        sequential=values.get("sequential"),
        aspect_ratio=values.get("aspect_ratio"),
        image_size=values.get("image_size"),
    )
    return endpoint, request


def _run_batch(args: argparse.Namespace) -> None:
    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        raise ImggenError(f"JSONL 文件不存在: {input_path}")
    if not args.out_dir:
        raise ImggenError("generate-batch 必须指定 --out-dir")
    if not 1 <= args.concurrency <= 25:
        raise ImggenError("--concurrency 必须在 1 到 25 之间")
    jobs: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        input_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImggenError(f"JSONL 第 {line_number} 行无效: {exc}") from exc
        if not isinstance(job, dict) or not str(job.get("prompt", "")).strip():
            raise ImggenError(f"JSONL 第 {line_number} 行必须是含 prompt 的对象")
        forbidden = {key for key in ("provider", "endpoint", "config") if key in job}
        if forbidden:
            raise ImggenError(
                f"JSONL 第 {line_number} 行不能切换 {', '.join(sorted(forbidden))}；"
                "batch 固定使用命令行 endpoint"
            )
        job["_line"] = line_number
        jobs.append(job)
    if not jobs:
        raise ImggenError("JSONL 中没有任务")

    def run(index: int, job: dict[str, Any]) -> tuple[int, list[Path]]:
        merged = dict(job)
        nested_fields = merged.pop("fields", {})
        if nested_fields and not isinstance(nested_fields, dict):
            raise ImggenError(f"job {index + 1} 的 fields 必须是对象")
        for field in PROMPT_FIELDS:
            if field not in merged and field in nested_fields:
                merged[field] = nested_fields[field]
        merged["prompt_pos"] = None
        merged["prompt_file"] = None
        endpoint, request = _request_from_args(args, "generate", merged)
        output = merged.get("out") or merged.get("output")
        if output:
            # Batch job outputs are filenames under --out-dir.  Matching the
            # legacy CLI here also prevents a job from escaping the batch root.
            output = str(Path(args.out_dir) / Path(str(output)).name)
        else:
            extension = request.output_format or "png"
            output = str(Path(args.out_dir) / f"job_{index + 1}.{extension}")
        if args.dry_run:
            validate_request(request)
            _print_dry_run(endpoint, request, output, None)
            return index, []
        preflight_outputs(
            request.n,
            output,
            None,
            request.output_format,
            args.force,
            args.downscale_max_dim,
            args.downscale_suffix,
        )
        artifacts = execute(endpoint, request, args.max_attempts)
        return index, save_artifacts(
            artifacts,
            output,
            None,
            request.output_format,
            args.force,
            args.downscale_max_dim,
            args.downscale_suffix,
        )

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(run, index, job): (index, job) for index, job in enumerate(jobs)
        }
        for future in as_completed(futures):
            index, job = futures[future]
            try:
                _, paths = future.result()
                for path in paths:
                    print(path)
            except Exception as exc:  # report all batch failures together
                failures.append(f"job {index + 1} (line {job['_line']}): {exc}")
                if args.fail_fast:
                    for pending in futures:
                        pending.cancel()
                    break
    if failures:
        raise ImggenError("批量任务失败:\n" + "\n".join(failures))


def _interactive_start(args: argparse.Namespace) -> None:
    reference = Path(args.image).expanduser()
    endpoint, request, prompt, markup = _interactive_request(args, reference)
    _require_interactive(endpoint, request)
    validate_request(request)
    store = SessionStore(args.session)
    if args.dry_run:
        output = args.output or str(
            store.path.with_name(f"{store.path.stem}-turn-1.png")
        )
        _print_dry_run(endpoint, request, output, args.out_dir)
        return
    data = store.create(
        endpoint.provider_key, endpoint.endpoint_key, request.model.name, reference
    )
    _perform_session_turn(store, data, endpoint, request, prompt, markup, args)


def _interactive_edit(args: argparse.Namespace) -> None:
    store = SessionStore(args.session)
    data = store.load()
    reference = store.latest_reference(data)
    args.provider = args.provider or data["provider"]
    args.endpoint = args.endpoint or data["endpoint"]
    args.model = args.model or data["model"]
    _assert_session_identity(data, args.provider, args.endpoint, args.model)
    endpoint, request, prompt, markup = _interactive_request(args, reference)
    _require_interactive(endpoint, request)
    if args.dry_run:
        validate_request(request)
        _print_dry_run(endpoint, request, args.output, args.out_dir)
        return
    _perform_session_turn(store, data, endpoint, request, prompt, markup, args)


def _interactive_retry(args: argparse.Namespace) -> None:
    store = SessionStore(args.session)
    data = store.load()
    candidates = [
        turn for turn in data["turns"] if turn.get("status") in {"pending", "failed"}
    ]
    if not candidates:
        raise ImggenError("会话中没有可重试的 pending/failed turn")
    turn = candidates[-1]
    endpoint = get_endpoint_config(
        data["provider"], data["endpoint"], config_path=args.config
    )
    policy = endpoint.resolve_model(data["model"], "edit")
    request = ImageRequest(
        operation="edit",
        prompt=turn["rendered_prompt"],
        model=policy,
        references=[Path(turn["reference"])],
        **dict(turn.get("request_options", {})),
    )
    _require_interactive(endpoint, request)
    index = data["turns"].index(turn)
    turn["status"] = "pending"
    store.save(data)
    try:
        output = args.output or str(
            store.path.with_name(f"{store.path.stem}-turn-{turn['index']}.png")
        )
        preflight_outputs(1, output, args.out_dir, None, args.force)
        artifacts = execute(endpoint, request, args.max_attempts)
        paths = save_artifacts(artifacts, output, args.out_dir, None, args.force)
        store.finish_turn(data, index, paths)
        for path in paths:
            print(path)
    except Exception as exc:
        store.fail_turn(data, index, exc)
        raise


def _interactive_show(args: argparse.Namespace) -> None:
    print(json.dumps(SessionStore(args.session).load(), ensure_ascii=False, indent=2))


def _chroma_key(args: argparse.Namespace) -> None:
    result = remove_chroma_key(
        args.input,
        args.out,
        key_color=args.key_color,
        tolerance=args.tolerance,
        auto_key=args.auto_key,
        soft_matte=args.soft_matte,
        transparent_threshold=args.transparent_threshold,
        opaque_threshold=args.opaque_threshold,
        edge_feather=args.edge_feather,
        edge_contract=args.edge_contract,
        spill_cleanup=args.spill_cleanup,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _remove_background(args: argparse.Namespace) -> None:
    result = remove_background(
        args.input,
        args.out,
        config_path=args.config,
        method=args.method,
        model=args.model,
        parameters_json=args.parameters_json,
        reprocess=args.reprocess,
        use_matting=not args.no_matting,
        fallback=not args.no_fallback,
        fallback_key_color=args.fallback_key_color,
        fallback_auto_key=args.fallback_auto_key,
        fallback_tolerance=args.fallback_tolerance,
        fallback_transparent_threshold=args.fallback_transparent_threshold,
        fallback_opaque_threshold=args.fallback_opaque_threshold,
        fallback_edge_feather=args.fallback_edge_feather,
        fallback_edge_contract=args.fallback_edge_contract,
        force=args.force,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _interactive_request(
    args: argparse.Namespace, reference: Path
) -> tuple[Any, ImageRequest, str, Markup]:
    points = [parse_point(value) for value in args.point]
    boxes = [parse_bbox(value) for value in args.bbox]
    markup = normalize_markup(points, boxes, parse_canvas_size(args.canvas_size))
    raw_prompt = read_prompt(args.prompt_pos, args.prompt, args.prompt_file)
    args.prompt_pos = marked_prompt(raw_prompt, markup)
    args.prompt = None
    args.prompt_file = None
    args.image = [str(reference)]
    endpoint, request = _request_from_args(args, "edit")
    return endpoint, request, raw_prompt, markup


def _perform_session_turn(
    store: SessionStore,
    data: dict[str, Any],
    endpoint: Any,
    request: ImageRequest,
    raw_prompt: str,
    markup: Markup,
    args: argparse.Namespace,
) -> None:
    validate_request(request)
    reference = request.references[0]
    index = store.begin_turn(
        data,
        raw_prompt,
        request.prompt,
        reference,
        markup,
        _session_request_options(request),
    )
    try:
        turn_number = data["turns"][index]["index"]
        output = args.output or str(
            store.path.with_name(f"{store.path.stem}-turn-{turn_number}.png")
        )
        preflight_outputs(
            request.n,
            output,
            args.out_dir,
            request.output_format,
            args.force,
            args.downscale_max_dim,
            args.downscale_suffix,
        )
        artifacts = execute(endpoint, request, args.max_attempts)
        paths = save_artifacts(
            artifacts,
            output,
            args.out_dir,
            request.output_format,
            args.force,
            args.downscale_max_dim,
            args.downscale_suffix,
        )
        store.finish_turn(data, index, paths)
        for path in paths:
            print(path)
    except Exception as exc:
        store.fail_turn(data, index, exc)
        raise


def _require_interactive(endpoint: Any, request: ImageRequest) -> None:
    if endpoint.adapter != "seedream":
        raise CapabilityError("interactive 会话只允许 adapter=seedream")
    if "interactive_edit" not in request.model.capabilities:
        raise CapabilityError(
            f"模型 '{request.model.name}' 未声明 interactive_edit 能力"
        )


def _session_request_options(request: ImageRequest) -> dict[str, Any]:
    names = (
        "n",
        "size",
        "quality",
        "background",
        "output_format",
        "output_compression",
        "moderation",
        "input_fidelity",
        "seed",
        "stream",
        "watermark",
        "sequential",
        "aspect_ratio",
        "image_size",
    )
    return {
        name: getattr(request, name)
        for name in names
        if getattr(request, name) is not None
    }


def _assert_session_identity(
    data: dict[str, Any], provider: str, endpoint: str, model: str
) -> None:
    expected = (data["provider"], data["endpoint"], data["model"])
    actual = (provider, endpoint, model)
    if actual != expected:
        raise ImggenError(
            f"会话已固定 provider/endpoint/model={expected}，拒绝切换到 {actual}"
        )


def _print_dry_run(
    endpoint: Any, request: ImageRequest, output: str | None, out_dir: str | None
) -> None:
    paths = output_paths(request.n, output, out_dir, request.output_format)
    print(
        json.dumps(
            {
                "provider": endpoint.provider_key,
                "endpoint": endpoint.endpoint_key,
                "adapter": endpoint.adapter,
                "base_url": endpoint.base_url,
                "model": request.model.name,
                "api_model": request.model.api_model,
                "operation": request.operation,
                "prompt": request.prompt,
                "references": [str(path) for path in request.references],
                "mask": str(request.mask) if request.mask else None,
                "n": request.n,
                "size": request.size,
                "quality": request.quality,
                "output_format": request.output_format,
                "outputs": [str(path) for path in paths],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
        return 0
    except (ImggenError, FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
