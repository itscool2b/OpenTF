"""Thin async wrapper around the Anthropic SDK.

Centralizes model selection, retry logic with exponential backoff + jitter,
token usage tracking (including prompt cache metrics), and streaming support.
Single swap point for future provider changes.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import anthropic

from opentf.auth.credentials import CredentialManager

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 8192


@dataclass
class TokenUsage:
    """Tracks cumulative token usage across requests."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_write: int = 0,
        cache_read: int = 0,
    ) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_write_tokens += cache_write
        self.cache_read_tokens += cache_read


@dataclass
class LLMClient:
    """Async Anthropic client with retry, caching, streaming, and token tracking."""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.7
    max_retries: int = 3
    base_delay: float = 1.0
    usage: TokenUsage = field(default_factory=TokenUsage)
    _client: anthropic.AsyncAnthropic | None = field(default=None, repr=False)
    _credentials: CredentialManager = field(default_factory=CredentialManager, repr=False)

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            key = self.api_key or self._credentials.resolve_api_key()
            if not key:
                raise RuntimeError(
                    "No API key found. Run 'opentf' to set up your key, "
                    "or set the ANTHROPIC_API_KEY environment variable."
                )
            self._client = anthropic.AsyncAnthropic(api_key=key)
        return self._client

    def reset_client(self) -> None:
        """Force re-creation of the client (e.g. after key change)."""
        self._client = None

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
    ) -> dict[str, Any]:
        """Build kwargs for messages.create() / messages.stream()."""
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "messages": messages,
        }

        # System prompt -- convert to cacheable content blocks if caching
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

        # Tools -- mark last tool with cache_control for caching
        if tools:
            if cache:
                tools = [*tools]
                tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = tools

        return kwargs

    def _track_usage(self, usage: Any) -> None:
        """Extract and accumulate token usage from API response."""
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.usage.add(
            usage.input_tokens,
            usage.output_tokens,
            cache_write=cache_write,
            cache_read=cache_read,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
    ) -> anthropic.types.Message:
        """Send a completion request with retry logic.

        Uses exponential backoff with jitter on rate limit / server errors.
        When cache=True, system prompt and tools are marked for prompt caching.
        """
        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools, cache,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.messages.create(**kwargs)
                self._track_usage(response.usage)
                return response
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = self.base_delay * (2**attempt) + random.uniform(0, 1)
                log.warning(
                    "LLM request failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1, self.max_retries, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> anthropic.types.Message:
        """Stream a completion, calling on_text for each text delta.

        Returns the final assembled Message (same shape as complete()).
        Falls back to complete() if on_text is None.
        """
        if on_text is None:
            return await self.complete(
                messages, system=system, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools, cache=cache,
            )

        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools, cache,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
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

                self._track_usage(response.usage)
                return response
            except (
                anthropic.RateLimitError,
                anthropic.InternalServerError,
                anthropic.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = self.base_delay * (2**attempt) + random.uniform(0, 1)
                log.warning(
                    "Stream request failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1, self.max_retries, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def complete_text(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        **kwargs: Any,
    ) -> str:
        """Convenience method that returns just the text content."""
        response = await self.complete(messages, system=system, **kwargs)
        return response.content[0].text  # type: ignore[union-attr]
