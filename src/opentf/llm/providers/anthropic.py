"""Anthropic provider backend.

Extracted from the original LLMClient -- wraps the Anthropic SDK
and converts responses to provider-agnostic LLMResponse types.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Awaitable

import anthropic

from opentf.llm.types import LLMResponse, TextBlock, ToolUseBlock, Usage

log = logging.getLogger(__name__)


def _to_llm_response(msg: anthropic.types.Message) -> LLMResponse:
    """Convert an Anthropic Message to our provider-agnostic LLMResponse."""
    blocks: list[TextBlock | ToolUseBlock] = []
    for block in msg.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            blocks.append(ToolUseBlock(
                id=block.id,
                name=block.name,
                input=block.input,
            ))

    usage = Usage(
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        cache_creation_input_tokens=getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
    )

    return LLMResponse(
        content=blocks,
        usage=usage,
        stop_reason=msg.stop_reason,
        model=msg.model,
    )


class AnthropicProvider:
    """Anthropic Claude API provider with prompt caching support."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            key = self._api_key
            if not key:
                import os
                key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                raise RuntimeError(
                    "No Anthropic API key found. Set ANTHROPIC_API_KEY "
                    "or configure via 'opentf' onboarding."
                )
            self._client = anthropic.AsyncAnthropic(api_key=key)
        return self._client

    def reset_client(self) -> None:
        self._client = None

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        cache: bool,
    ) -> dict[str, Any]:
        """Build kwargs for messages.create() / messages.stream()."""
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        if system:
            if cache and isinstance(system, str):
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                kwargs["system"] = system

        if tools:
            if cache:
                tools = [*tools]
                tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = tools

        return kwargs

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools, cache,
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.messages.create(**kwargs)
                return _to_llm_response(response)
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "Anthropic request failed (attempt %d/3): %s. Retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        if on_text is None:
            return await self.complete(
                messages, system=system, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools, cache=cache,
            )

        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools, cache,
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.client.messages.stream(**kwargs) as stream_mgr:
                    async for event in stream_mgr:
                        if (
                            hasattr(event, "type")
                            and event.type == "content_block_delta"
                            and hasattr(event.delta, "text")
                        ):
                            await on_text(event.delta.text)

                    response = await stream_mgr.get_final_message()

                return _to_llm_response(response)
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "Anthropic stream failed (attempt %d/3): %s. Retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]
