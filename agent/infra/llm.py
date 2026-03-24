"""
LLM service layer for agent package.

Provides complete/stream/get_llm_config without external src.* dependency.
Uses OpenAI-compatible API via the openai SDK.
"""

import os
from dataclasses import dataclass
from typing import AsyncGenerator


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    api_version: str | None = None
    binding: str = "openai"


_config_override: LLMConfig | None = None


def configure(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_version: str | None = None,
    binding: str = "openai",
) -> None:
    """Set LLM config programmatically (takes precedence over env vars)."""
    global _config_override
    _config_override = LLMConfig(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        base_url=base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=model or os.environ.get("OPENAI_MODEL", "gpt-4o"),
        api_version=api_version or os.environ.get("OPENAI_API_VERSION"),
        binding=binding,
    )


def get_llm_config() -> LLMConfig:
    """Get current LLM config (programmatic override > env vars)."""
    if _config_override is not None:
        return _config_override
    return LLMConfig(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        api_version=os.environ.get("OPENAI_API_VERSION"),
    )


def get_token_limit_kwargs(model: str, max_tokens: int) -> dict:
    """Return the correct max-token kwarg name for the model."""
    if any(tag in model for tag in ("gpt-4o", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def supports_response_format(binding: str, model: str) -> bool:
    """Check if the model supports response_format (JSON mode)."""
    # Most modern OpenAI-compatible APIs support it
    return True


async def complete(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    max_retries: int = 2,
    messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Call LLM (non-streaming) via OpenAI-compatible API."""
    from openai import AsyncOpenAI

    cfg = get_llm_config()
    client = AsyncOpenAI(
        api_key=api_key or cfg.api_key,
        base_url=base_url or cfg.base_url,
        max_retries=max_retries,
    )
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

    call_kwargs: dict = {"model": model or cfg.model, "messages": messages}
    for k in ("temperature", "max_tokens", "max_completion_tokens", "response_format"):
        if k in kwargs:
            call_kwargs[k] = kwargs[k]

    resp = await client.chat.completions.create(**call_kwargs)
    return resp.choices[0].message.content or ""


async def stream(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    max_retries: int = 2,
    messages: list[dict] | None = None,
    **kwargs,
) -> AsyncGenerator[str, None]:
    """Stream LLM response via OpenAI-compatible API."""
    from openai import AsyncOpenAI

    cfg = get_llm_config()
    client = AsyncOpenAI(
        api_key=api_key or cfg.api_key,
        base_url=base_url or cfg.base_url,
        max_retries=max_retries,
    )
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

    call_kwargs: dict = {"model": model or cfg.model, "messages": messages, "stream": True}
    for k in ("temperature", "max_tokens", "max_completion_tokens"):
        if k in kwargs:
            call_kwargs[k] = kwargs[k]

    resp = await client.chat.completions.create(**call_kwargs)
    async for chunk in resp:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta
