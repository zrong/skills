from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import imggen.config as config_module
from imggen.config import find_config, get_endpoint_config
from imggen.models import (
    CapabilityError,
    ConfigError,
    ImageArtifact,
    ImageRequest,
    ImggenError,
    ModelPolicy,
)
from imggen.service import execute, validate_request


def test_exact_model_allowlist(config_file: Path) -> None:
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    assert endpoint.adapter == "openai"
    assert endpoint.api_key == "test-openai"
    assert endpoint.resolve_model(None, "generate").name == "gpt-image-test"
    with pytest.raises(ConfigError, match="未被 endpoint"):
        endpoint.resolve_model("remote-but-unconfigured", "generate")


def test_each_endpoint_owns_credentials(config_file: Path) -> None:
    openai = get_endpoint_config("test", "openai", config_path=config_file)
    gemini = get_endpoint_config("test", "gemini", config_path=config_file)
    seedream = get_endpoint_config("test", "seedream", config_path=config_file)
    assert {openai.api_key, gemini.api_key, seedream.api_key} == {
        "test-openai",
        "test-gemini",
        "test-seedream",
    }


def test_model_adapter_must_match_endpoint() -> None:
    with pytest.raises(ConfigError, match="不一致"):
        ModelPolicy.from_dict(
            "wrong-adapter",
            {"adapter": "gemini", "operations": ["generate"]},
            endpoint_adapter="openai",
        )


def test_config_lookup_priority(tmp_path: Path, monkeypatch) -> None:
    cwd = tmp_path / "project" / "work"
    git_root = tmp_path / "project"
    skill_dir = tmp_path / "skill"
    cwd.mkdir(parents=True)
    (git_root / ".git").mkdir()
    skill_dir.mkdir()
    explicit = tmp_path / "explicit.toml"
    env_config = tmp_path / "env.toml"
    cwd_config = cwd / "agent_config.toml"
    skill_config = skill_dir / "agent_config.toml"
    git_config = git_root / "agent_config.toml"
    for path in (explicit, env_config, cwd_config, skill_config, git_config):
        path.write_text("[image-generation]\n", encoding="utf-8")

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(config_module, "SKILL_DIR", skill_dir)
    monkeypatch.setenv("IMAGEGEN_CONFIG", str(env_config))
    assert find_config(explicit) == explicit.resolve()
    assert find_config() == env_config.resolve()

    monkeypatch.delenv("IMAGEGEN_CONFIG")
    assert find_config() == cwd_config.resolve()
    cwd_config.unlink()
    assert find_config() == skill_config.resolve()
    skill_config.unlink()
    assert find_config() == git_config.resolve()


def test_explicit_missing_config_does_not_fall_back(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "agent_config.toml").write_text(
        "[image-generation]\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="missing.toml"):
        find_config(tmp_path / "missing.toml")


def test_capability_and_limit_validation(config_file: Path, tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"image")
    endpoint = get_endpoint_config("test", "seedream", config_path=config_file)
    policy = endpoint.resolve_model(None, "edit")
    request = ImageRequest(
        operation="edit",
        prompt="edit",
        model=policy,
        references=[reference],
        n=2,
    )
    with pytest.raises(CapabilityError, match="最多允许 1"):
        validate_request(request)


def test_unlisted_option_rejected_before_adapter(config_file: Path) -> None:
    endpoint = get_endpoint_config("test", "gemini", config_path=config_file)
    policy = endpoint.resolve_model(None, "generate")
    request = ImageRequest(
        operation="generate", prompt="x", model=policy, quality="high"
    )
    with pytest.raises(CapabilityError, match="quality"):
        validate_request(request)


def test_transparent_output_requires_alpha_format(config_file: Path) -> None:
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    policy = endpoint.resolve_model(None, "generate")
    policy = replace(
        policy,
        capabilities=policy.capabilities | {"background", "output_format"},
        output_formats=("png", "jpeg", "webp"),
    )
    with pytest.raises(ImggenError, match="png 或 webp"):
        validate_request(
            ImageRequest(
                operation="generate",
                prompt="transparent",
                model=policy,
                background="transparent",
                output_format="jpeg",
            )
        )
    validate_request(
        ImageRequest(
            operation="generate",
            prompt="transparent",
            model=policy,
            background="transparent",
            output_format="png",
        )
    )


def test_config_driven_gpt_image_size_rules(config_file: Path) -> None:
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    policy = replace(
        endpoint.resolve_model(None, "generate"),
        sizes=(),
        options={
            "size_rules": {
                "multiple_of": 16,
                "max_edge": 3840,
                "max_ratio": 3,
                "min_pixels": 655360,
                "max_pixels": 8294400,
            }
        },
    )
    with pytest.raises(CapabilityError, match="16 的倍数"):
        validate_request(
            ImageRequest(
                operation="generate",
                prompt="bad size",
                model=policy,
                size="1537x1024",
            )
        )
    validate_request(
        ImageRequest(
            operation="generate",
            prompt="valid size",
            model=policy,
            size="1536x1024",
        )
    )


def test_retry_and_error_message_do_not_leak_key(
    config_file: Path, monkeypatch
) -> None:
    endpoint = get_endpoint_config("test", "openai", config_path=config_file)
    request = ImageRequest(
        operation="generate",
        prompt="x",
        model=endpoint.resolve_model(None, "generate"),
    )

    class FlakyAdapter:
        attempts = 0

        def execute(self, _request):
            self.attempts += 1
            if self.attempts == 1:
                raise httpx.ReadTimeout("temporary")
            return [ImageArtifact(b"ok")]

    adapter = FlakyAdapter()
    monkeypatch.setattr("imggen.service.create_adapter", lambda _endpoint: adapter)
    monkeypatch.setattr("imggen.service.time.sleep", lambda _seconds: None)
    assert execute(endpoint, request, max_attempts=2)[0].data == b"ok"
    assert adapter.attempts == 2

    class UnauthorizedAdapter:
        def execute(self, _request):
            raw_request = httpx.Request(
                "POST", "https://example.test/v1/images/generations"
            )
            response = httpx.Response(
                401,
                request=raw_request,
                json={"error": f"invalid key {endpoint.api_key}"},
            )
            raise httpx.HTTPStatusError(
                "unauthorized", request=raw_request, response=response
            )

    monkeypatch.setattr(
        "imggen.service.create_adapter", lambda _endpoint: UnauthorizedAdapter()
    )
    with pytest.raises(Exception) as exc_info:
        execute(endpoint, request, max_attempts=1)
    assert "test-openai" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
