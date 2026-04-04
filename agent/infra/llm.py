"""
LLM service layer for agent package.

Provides complete/stream/get_llm_config without external src.* dependency.
Uses OpenAI-compatible API via the openai SDK.
"""

import os
import json
from dataclasses import dataclass, field
from typing import AsyncGenerator, Any


@dataclass
class ToolCall:
    """Parsed tool call from LLM response."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolUseResponse:
    """Response from complete_with_tools — may contain content, tool_calls, or both."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""  # DeepSeek V3.2 thinking chain

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    api_version: str | None = None
    binding: str = "openai"
    default_headers: dict[str, str] | None = None


_config_override: LLMConfig | None = None


def configure(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_version: str | None = None,
    binding: str = "openai",
    default_headers: dict[str, str] | None = None,
) -> None:
    """Set LLM config programmatically (takes precedence over env vars)."""
    global _config_override
    _config_override = LLMConfig(
        api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        base_url=base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=model or os.environ.get("OPENAI_MODEL", "gpt-4o"),
        api_version=api_version or os.environ.get("OPENAI_API_VERSION"),
        binding=binding,
        default_headers=default_headers or _load_default_headers_from_env(),
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
        default_headers=_load_default_headers_from_env(),
    )


def _load_default_headers_from_env() -> dict[str, str] | None:
    """Load optional OpenAI-compatible default headers from env vars."""
    headers: dict[str, str] = {}

    raw_json = os.environ.get("OPENAI_DEFAULT_HEADERS_JSON", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if value is not None:
                        headers[str(key)] = str(value)
        except Exception:
            # Ignore malformed env JSON and fall back to simple per-header vars.
            pass

    if os.environ.get("OPENAI_USER_AGENT"):
        headers["User-Agent"] = os.environ["OPENAI_USER_AGENT"]
    if os.environ.get("OPENAI_HTTP_REFERER"):
        headers["HTTP-Referer"] = os.environ["OPENAI_HTTP_REFERER"]

    return headers or None


def get_token_limit_kwargs(model: str, max_tokens: int) -> dict:
    """Return the correct max-token kwarg name for the model."""
    if any(tag in model for tag in ("gpt-4o", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def supports_response_format(binding: str, model: str) -> bool:
    """Check if the model supports response_format (JSON mode)."""
    # Reasoning models (deepseek-reasoner, o1, etc.) don't support json mode
    if any(tag in model for tag in ("reasoner", "o1-preview", "o1-mini")):
        return False
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
    default_headers: dict[str, str] | None = None,
    **kwargs,
) -> str:
    """Call LLM (non-streaming) via OpenAI-compatible API."""
    from openai import AsyncOpenAI

    cfg = get_llm_config()
    client_kwargs: dict[str, Any] = {
        "api_key": api_key or cfg.api_key,
        "base_url": base_url or cfg.base_url,
        "max_retries": max_retries,
    }
    headers = default_headers or cfg.default_headers
    if headers:
        client_kwargs["default_headers"] = headers
    client = AsyncOpenAI(
        **client_kwargs,
        timeout=300.0,  # reasoning models can be slow
    )
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

    call_kwargs: dict = {"model": model or cfg.model, "messages": messages}
    for k in ("temperature", "max_tokens", "max_completion_tokens", "response_format",
              "tools", "tool_choice"):
        if k in kwargs:
            call_kwargs[k] = kwargs[k]

    resp = await client.chat.completions.create(**call_kwargs)
    return resp.choices[0].message.content or ""


async def complete_with_tools(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    api_version: str | None = None,
    max_retries: int = 2,
    messages: list[dict] | None = None,
    default_headers: dict[str, str] | None = None,
    **kwargs,
) -> ToolUseResponse:
    """Call LLM with tool use support. Returns ToolUseResponse with content and/or tool_calls."""
    from openai import AsyncOpenAI

    cfg = get_llm_config()
    client_kwargs: dict[str, Any] = {
        "api_key": api_key or cfg.api_key,
        "base_url": base_url or cfg.base_url,
        "max_retries": max_retries,
    }
    headers = default_headers or cfg.default_headers
    if headers:
        client_kwargs["default_headers"] = headers
    client = AsyncOpenAI(**client_kwargs, timeout=300.0)

    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

    call_kwargs: dict = {"model": model or cfg.model, "messages": messages}
    for k in ("temperature", "max_tokens", "max_completion_tokens", "response_format",
              "tools", "tool_choice"):
        if k in kwargs:
            call_kwargs[k] = kwargs[k]
    # DeepSeek V3.2 thinking mode: pass via extra_body for OpenAI SDK
    if "thinking" in kwargs:
        call_kwargs["extra_body"] = {"thinking": kwargs["thinking"]}

    resp = await client.chat.completions.create(**call_kwargs)
    msg = resp.choices[0].message

    tool_calls: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {"_raw": tc.function.arguments}
            tool_calls.append(ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=args,
            ))

    return ToolUseResponse(
        content=msg.content or "",
        tool_calls=tool_calls,
        reasoning_content=getattr(msg, "reasoning_content", "") or "",
    )


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
    client_kwargs: dict[str, Any] = {
        "api_key": api_key or cfg.api_key,
        "base_url": base_url or cfg.base_url,
        "max_retries": max_retries,
    }
    if cfg.default_headers:
        client_kwargs["default_headers"] = cfg.default_headers
    client = AsyncOpenAI(**client_kwargs)
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
