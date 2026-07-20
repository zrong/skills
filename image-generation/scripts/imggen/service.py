"""Provider-neutral validation and retry orchestration."""

from __future__ import annotations

import time
import re
import sys
from pathlib import Path

import httpx

from imggen.adapters import create_adapter
from imggen.models import (
    CapabilityError,
    EndpointConfig,
    ImageArtifact,
    ImageRequest,
    ImggenError,
)


_OPTION_CAPABILITIES = {
    "size": "size",
    "quality": "quality",
    "background": "background",
    "output_format": "output_format",
    "output_compression": "output_compression",
    "moderation": "moderation",
    "input_fidelity": "input_fidelity",
    "seed": "seed",
    "stream": "stream",
    "watermark": "watermark",
    "sequential": "sequential",
    "aspect_ratio": "aspect_ratio",
    "image_size": "image_size",
}


def validate_request(request: ImageRequest) -> None:
    if not request.prompt.strip():
        raise ImggenError("Prompt 不能为空")
    if request.n < 1 or request.n > 10:
        raise ImggenError("--n 必须在 1 到 10 之间")
    if request.n > request.model.max_outputs:
        raise CapabilityError(
            f"模型 '{request.model.name}' 最多允许 {request.model.max_outputs} 张输出，本次请求 {request.n}"
        )
    if request.operation == "edit" and not request.references:
        raise ImggenError("edit 至少需要一个 --image")
    if request.operation == "generate" and request.references:
        raise ImggenError("generate 不接受 --image；请使用 edit")
    if len(request.references) > request.model.max_references:
        raise CapabilityError(
            f"模型 '{request.model.name}' 最多允许 {request.model.max_references} 张参考图，"
            f"本次请求 {len(request.references)}"
        )
    if (
        len(request.references) > 1
        and "multi_reference" not in request.model.capabilities
    ):
        raise CapabilityError(
            f"模型 '{request.model.name}' 未声明 multi_reference 能力"
        )
    for path in request.references:
        _validate_file(path, "参考图")
    if request.mask:
        _validate_file(request.mask, "mask")
        if "mask" not in request.model.capabilities:
            raise CapabilityError(f"模型 '{request.model.name}' 未声明 mask 能力")
    for field, capability in _OPTION_CAPABILITIES.items():
        value = getattr(request, field)
        if value is not None and capability not in request.model.capabilities:
            raise CapabilityError(
                f"模型 '{request.model.name}' 未声明 {capability} 能力，不能使用 --{field.replace('_', '-')}"
            )
    if (
        request.output_compression is not None
        and not 0 <= request.output_compression <= 100
    ):
        raise ImggenError("--output-compression 必须在 0 到 100 之间")
    if request.size and request.model.sizes and request.size not in request.model.sizes:
        raise CapabilityError(
            f"模型 '{request.model.name}' 不允许 size={request.size}；允许: {', '.join(request.model.sizes)}"
        )
    if request.size:
        _validate_size_rules(
            request.size,
            request.model.name,
            request.model.options.get("size_rules", {}),
        )
    if (
        request.quality
        and request.model.qualities
        and request.quality not in request.model.qualities
    ):
        raise CapabilityError(
            f"模型 '{request.model.name}' 不允许 quality={request.quality}；允许: "
            f"{', '.join(request.model.qualities)}"
        )
    if request.output_format and request.model.output_formats:
        normalized = "jpeg" if request.output_format == "jpg" else request.output_format
        if normalized not in request.model.output_formats:
            raise CapabilityError(
                f"模型 '{request.model.name}' 不允许 output_format={normalized}；允许: "
                f"{', '.join(request.model.output_formats)}"
            )
    if request.background == "transparent" and request.output_format not in {
        "png",
        "webp",
    }:
        raise ImggenError("透明背景要求 --output-format png 或 webp")
    if request.background is not None and request.background not in {
        "transparent",
        "opaque",
        "auto",
    }:
        raise ImggenError("--background 必须是 transparent/opaque/auto")
    if request.input_fidelity is not None and request.input_fidelity not in {
        "low",
        "high",
    }:
        raise ImggenError("--input-fidelity 必须是 low/high")


def execute(
    endpoint: EndpointConfig, request: ImageRequest, max_attempts: int = 3
) -> list[ImageArtifact]:
    """Validate before adapter creation/network, then retry transient failures."""
    validate_request(request)
    if not 1 <= max_attempts <= 10:
        raise ImggenError("--max-attempts 必须在 1 到 10 之间")
    adapter = create_adapter(endpoint)
    for attempt in range(1, max_attempts + 1):
        try:
            return adapter.execute(request)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == max_attempts:
                raise ImggenError(
                    f"网络请求失败（已尝试 {attempt} 次）: "
                    f"{_redact_secret(str(exc), endpoint.api_key)}"
                ) from None
            time.sleep(min(2 ** (attempt - 1), 8))
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in {408, 409, 429, 500, 502, 503, 504}
                or attempt == max_attempts
            ):
                detail = _redact_secret(
                    exc.response.text.strip().replace("\n", " ")[:1200],
                    endpoint.api_key,
                )
                suffix = f": {detail}" if detail else ""
                raise ImggenError(
                    f"图片 API 返回 HTTP {exc.response.status_code} {exc.response.reason_phrase}{suffix}"
                ) from None
            retry_after = exc.response.headers.get("retry-after")
            time.sleep(
                float(retry_after)
                if retry_after and retry_after.isdigit()
                else min(2 ** (attempt - 1), 8)
            )
    raise AssertionError("unreachable")


def _redact_secret(text: str, secret: str) -> str:
    if secret:
        return text.replace(secret, "[REDACTED]")
    return text


def _validate_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ImggenError(f"{label}不存在: {path}")
    if path.stat().st_size == 0:
        raise ImggenError(f"{label}为空文件: {path}")
    if path.stat().st_size > 50 * 1024 * 1024:
        print(f"警告: {label}超过 50MB: {path}", file=sys.stderr)


def _validate_size_rules(size: str, model_name: str, rules: object) -> None:
    if not isinstance(rules, dict) or not rules or size == "auto":
        return
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", size)
    if not match:
        raise CapabilityError(
            f"模型 '{model_name}' 的 size 必须是 auto 或 WIDTHxHEIGHT"
        )
    width, height = int(match.group(1)), int(match.group(2))
    multiple = int(rules.get("multiple_of", 1))
    if width % multiple or height % multiple:
        raise CapabilityError(f"模型 '{model_name}' 的宽高必须是 {multiple} 的倍数")
    max_edge = int(rules.get("max_edge", max(width, height)))
    if max(width, height) > max_edge:
        raise CapabilityError(f"模型 '{model_name}' 的最长边不能超过 {max_edge}")
    max_ratio = float(rules.get("max_ratio", max(width, height) / min(width, height)))
    if max(width, height) / min(width, height) > max_ratio:
        raise CapabilityError(f"模型 '{model_name}' 的长短边比例不能超过 {max_ratio}:1")
    pixels = width * height
    if pixels < int(rules.get("min_pixels", pixels)) or pixels > int(
        rules.get("max_pixels", pixels)
    ):
        raise CapabilityError(
            f"模型 '{model_name}' 的总像素必须在 {rules.get('min_pixels', 0)}.."
            f"{rules.get('max_pixels', 'unbounded')} 之间"
        )
