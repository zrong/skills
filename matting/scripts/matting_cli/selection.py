"""Live-capability-aware matting method selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import MattingConfig
from .errors import MattingError

KNOWN_MODEL_METHODS: dict[str, frozenset[str]] = {
    "birefnet-auto-quality": frozenset(
        {"birefnet", "birefnet_luma_restore", "birefnet_luma_tighten"}
    ),
    "birefnet-hr-matting": frozenset(
        {"birefnet", "birefnet_luma_restore", "birefnet_luma_tighten"}
    ),
    "birefnet-lite-2k": frozenset(
        {"birefnet", "birefnet_luma_restore", "birefnet_luma_tighten"}
    ),
    "birefnet-general": frozenset(
        {"birefnet", "birefnet_luma_restore", "birefnet_luma_tighten"}
    ),
    "corridorkey-refine": frozenset({"corridorkey_refine"}),
    "birefnet-auto-quality-corridorkey": frozenset(
        {
            "birefnet_corridorkey_refine",
            "birefnet_corridorkey_tighten",
            "birefnet_luma_corridorkey_refine",
        }
    ),
    "birefnet-hr-corridorkey": frozenset(
        {
            "birefnet_corridorkey_refine",
            "birefnet_corridorkey_tighten",
            "birefnet_luma_corridorkey_refine",
        }
    ),
    "inspyrenet-base": frozenset({"inspyrenet"}),
    "inspyrenet-fast": frozenset({"inspyrenet"}),
}

METHOD_MODELS: dict[str, tuple[str, ...]] = {
    "birefnet_corridorkey_refine": (
        "birefnet-auto-quality-corridorkey",
        "birefnet-hr-corridorkey",
    ),
    "birefnet_corridorkey_tighten": (
        "birefnet-auto-quality-corridorkey",
        "birefnet-hr-corridorkey",
    ),
    "birefnet_luma_corridorkey_refine": (
        "birefnet-auto-quality-corridorkey",
        "birefnet-hr-corridorkey",
    ),
    "corridorkey_refine": ("corridorkey-refine",),
    "birefnet": (
        "birefnet-auto-quality",
        "birefnet-hr-matting",
        "birefnet-general",
        "birefnet-lite-2k",
    ),
    "birefnet_luma_restore": (
        "birefnet-auto-quality",
        "birefnet-hr-matting",
        "birefnet-general",
        "birefnet-lite-2k",
    ),
    "birefnet_luma_tighten": (
        "birefnet-auto-quality",
        "birefnet-hr-matting",
        "birefnet-general",
        "birefnet-lite-2k",
    ),
    "inspyrenet": ("inspyrenet-base", "inspyrenet-fast"),
}


@dataclass(frozen=True)
class Selection:
    action: str
    method: str | None
    model: str | None
    confidence: str
    reasons: tuple[str, ...]
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def advertised(capabilities: dict[str, Any]) -> tuple[set[str], set[str]]:
    raw_methods = capabilities.get("methods", {})
    corridorkey = capabilities.get("corridorkey", {})
    corridorkey_known = isinstance(corridorkey, dict) and "available" in corridorkey
    corridorkey_available = corridorkey_known and corridorkey.get("available") is True
    if isinstance(raw_methods, dict):
        methods = {
            str(name)
            for name, details in raw_methods.items()
            if not (
                corridorkey_known
                and not corridorkey_available
                and (
                    "corridorkey" in str(name)
                    or (
                        isinstance(details, dict)
                        and details.get("requires_corridorkey") is True
                    )
                )
            )
        }
    elif isinstance(raw_methods, list):
        methods = {str(item) for item in raw_methods if str(item)}
    else:
        methods = set()
    raw_models = capabilities.get("models", [])
    models: set[str] = set()
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                key = str(item.get("model_key") or item.get("key") or "").strip()
            else:
                key = str(item).strip()
            if key:
                models.add(key)
    return methods, models


def compatibility(
    config: MattingConfig, capabilities: dict[str, Any]
) -> dict[str, frozenset[str]]:
    methods, models = advertised(capabilities)
    remote = capabilities.get("model_methods", {})
    result: dict[str, frozenset[str]] = {}
    for model in models:
        raw = remote.get(model) if isinstance(remote, dict) else None
        if isinstance(raw, list):
            allowed = frozenset(str(item) for item in raw if str(item) in methods)
        elif model in config.model_methods:
            allowed = config.model_methods[model] & methods
        else:
            allowed = KNOWN_MODEL_METHODS.get(model, frozenset()) & methods
        if allowed:
            result[model] = allowed
    return result


def available_pairs(
    config: MattingConfig, capabilities: dict[str, Any]
) -> list[dict[str, str]]:
    pairs = []
    for model, methods in sorted(compatibility(config, capabilities).items()):
        pairs.extend({"method": method, "model": model} for method in sorted(methods))
    return pairs


def _model_for(
    method: str, config: MattingConfig, compat: dict[str, frozenset[str]]
) -> str | None:
    candidates = []
    if config.default_model:
        candidates.append(config.default_model)
    candidates.extend(METHOD_MODELS.get(method, ()))
    candidates.extend(sorted(compat))
    for model in dict.fromkeys(candidates):
        if method in compat.get(model, frozenset()):
            return model
    return None


def select(
    inspection: dict[str, Any],
    config: MattingConfig,
    capabilities: dict[str, Any],
    *,
    method: str | None = None,
    model: str | None = None,
    parameters: dict[str, Any] | None = None,
    reprocess: bool = False,
) -> Selection:
    methods, models = advertised(capabilities)
    if not methods or not models:
        raise MattingError("matting-api 没有广告可用算法或模型")
    compat = compatibility(config, capabilities)
    supplied = dict(parameters or {})

    if (
        inspection.get("has_effective_alpha")
        and not reprocess
        and not method
        and not model
    ):
        return Selection(
            action="preserve_existing_alpha",
            method=None,
            model=None,
            confidence="high",
            reasons=("源图已经包含明显透明或半透明像素",),
            parameters=supplied,
        )

    if method or model:
        if not method or not model:
            raise MattingError("显式覆盖必须同时提供 --method 与 --model")
        if method not in methods:
            raise MattingError(f"算法未由服务广告: {method}")
        if model not in models:
            raise MattingError(f"模型未由服务广告: {model}")
        known = compat.get(model)
        if known is None:
            raise MattingError(
                f"无法证明模型 {model} 与算法 {method} 兼容；请在服务能力或配置中声明映射"
            )
        if method not in known:
            raise MattingError(f"模型 {model} 不兼容算法 {method}")
        return Selection(
            action="matting_api",
            method=method,
            model=model,
            confidence="explicit",
            reasons=("用户显式选择了实时可见的算法与模型",),
            parameters=_inferred_parameters(inspection, supplied),
        )

    screen = inspection.get("screen_color")
    if screen in {"green", "blue"}:
        candidates = [
            "birefnet_corridorkey_refine",
            "corridorkey_refine",
            "birefnet",
            "inspyrenet",
        ]
        screen_label = "绿" if screen == "green" else "蓝"
        reason = f"边框检测为均匀{screen_label}幕，优先保留细边缘并处理溢色"
        confidence = "high"
    elif inspection.get("likely_luma_effect"):
        candidates = ["birefnet_luma_restore", "birefnet", "inspyrenet"]
        reason = "边框明暗与全图亮度分布符合亮度型特效素材"
        confidence = "medium"
    else:
        candidates = ["birefnet", "inspyrenet"]
        reason = "未检测到可靠纯色幕布或已有 alpha，使用通用语义抠图"
        confidence = "medium"

    for candidate in candidates:
        if candidate not in methods:
            continue
        selected_model = _model_for(candidate, config, compat)
        if selected_model:
            return Selection(
                action="matting_api",
                method=candidate,
                model=selected_model,
                confidence=confidence,
                reasons=(reason, "算法和模型均来自本次实时能力交集"),
                parameters=_inferred_parameters(inspection, supplied),
            )
    raise MattingError("实时能力中没有可自动证明兼容的算法/模型组合；请显式指定")


def _inferred_parameters(
    inspection: dict[str, Any], supplied: dict[str, Any]
) -> dict[str, Any]:
    result = dict(supplied)
    screen = inspection.get("screen_color")
    if screen in {"green", "blue"}:
        result.setdefault("corridorkey_screen", screen)
        result.setdefault("key_mode", "manual")
        result.setdefault("manual_key_hex", inspection.get("border_color_hex"))
    if inspection.get("likely_luma_effect"):
        polarity = "bright" if inspection.get("border_dark_ratio", 0) >= 0.8 else "dark"
        result.setdefault("luma_polarity", polarity)
    return result
