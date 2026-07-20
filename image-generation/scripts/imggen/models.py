"""Core request and configuration models for imggen."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Operation = Literal["generate", "edit"]


class ImggenError(RuntimeError):
    """User-facing imggen error."""


class ConfigError(ImggenError):
    """Invalid or missing configuration."""


class CapabilityError(ImggenError):
    """A request uses a capability that was not explicitly allowed."""


@dataclass(frozen=True)
class ModelPolicy:
    """Strict per-model allowlist and capability declaration."""

    name: str
    adapter: str
    api_model: str
    operations: frozenset[str]
    capabilities: frozenset[str]
    sizes: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ()
    max_references: int = 0
    max_outputs: int = 1
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, name: str, data: dict[str, Any], endpoint_adapter: str | None = None
    ) -> "ModelPolicy":
        adapter = str(data.get("adapter", "")).lower()
        if adapter not in {"openai", "gemini", "seedream"}:
            raise ConfigError(
                f"模型 '{name}' 必须显式声明 adapter=openai/gemini/seedream"
            )
        if endpoint_adapter and adapter != endpoint_adapter:
            raise ConfigError(
                f"模型 '{name}' 声明 adapter={adapter}，与 endpoint adapter={endpoint_adapter} 不一致"
            )
        operations = frozenset(str(v) for v in data.get("operations", []))
        if not operations:
            raise ConfigError(f"模型 '{name}' 必须声明非空 operations")
        invalid = operations - {"generate", "edit"}
        if invalid:
            raise ConfigError(
                f"模型 '{name}' 包含未知 operations: {', '.join(sorted(invalid))}"
            )
        capabilities = frozenset(str(v) for v in data.get("capabilities", []))
        return cls(
            name=name,
            adapter=adapter,
            api_model=str(data.get("api_model", name)),
            operations=operations,
            capabilities=capabilities,
            sizes=tuple(str(v) for v in data.get("sizes", [])),
            qualities=tuple(str(v) for v in data.get("qualities", [])),
            output_formats=tuple(str(v) for v in data.get("output_formats", [])),
            max_references=int(data.get("max_references", 0)),
            max_outputs=int(data.get("max_outputs", 1)),
            options=dict(data.get("options", {})),
        )


@dataclass(frozen=True)
class EndpointConfig:
    """One independently authenticated API endpoint and its model allowlist."""

    provider_key: str
    provider_name: str
    endpoint_key: str
    adapter: str
    base_url: str
    api_key: str
    auth: str
    default_model: str
    models: dict[str, ModelPolicy]
    timeout: float = 180.0
    headers: dict[str, str] = field(default_factory=dict)

    def resolve_model(self, requested: str | None, operation: Operation) -> ModelPolicy:
        model_name = requested or self.default_model
        if not model_name:
            raise ConfigError(
                f"endpoint '{self.provider_key}/{self.endpoint_key}' 未配置 default_model，"
                "请通过 --model 指定 allowlist 中的模型"
            )
        policy = self.models.get(model_name)
        if policy is None:
            allowed = ", ".join(sorted(self.models)) or "(空)"
            raise ConfigError(
                f"模型 '{model_name}' 未被 endpoint '{self.provider_key}/{self.endpoint_key}' "
                f"允许；配置 allowlist: {allowed}"
            )
        if operation not in policy.operations:
            raise CapabilityError(
                f"模型 '{model_name}' 未允许操作 '{operation}'；允许: "
                f"{', '.join(sorted(policy.operations))}"
            )
        return policy


@dataclass
class ImageRequest:
    operation: Operation
    prompt: str
    model: ModelPolicy
    references: list[Path] = field(default_factory=list)
    mask: Path | None = None
    n: int = 1
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    output_compression: int | None = None
    moderation: str | None = None
    input_fidelity: str | None = None
    seed: int | None = None
    stream: bool | None = None
    watermark: bool | None = None
    sequential: str | None = None
    aspect_ratio: str | None = None
    image_size: str | None = None


@dataclass(frozen=True)
class ImageArtifact:
    data: bytes
    mime_type: str = "image/png"
    revised_prompt: str | None = None
