"""Multi-provider LLM client.

Thin facade that delegates to provider backends (Anthropic, OpenAI, Ollama).
Centralizes model selection, token tracking, and provides a stable interface
that all consumers (tool_loop, agents, etc.) use without caring about the
underlying provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from opentf.auth.credentials import CredentialManager
from opentf.llm.provider import LLMProvider
from opentf.llm.types import LLMResponse
from opentf.llm.registry import get_default_model

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
    """Multi-provider LLM client with token tracking.

    Delegates all LLM calls to a provider backend. Provider is lazily
    created on first use based on provider_name.
    """

    provider_name: str = "anthropic"
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.7
    max_retries: int = 3
    base_delay: float = 1.0
    usage: TokenUsage = field(default_factory=TokenUsage)
    _provider: LLMProvider | None = field(default=None, repr=False)
    _credentials: CredentialManager = field(default_factory=CredentialManager, repr=False)

    @property
    def provider(self) -> LLMProvider:
        """Lazily create the provider backend."""
        if self._provider is None:
            from opentf.llm.providers import create_provider

            # Resolve API key: explicit > credentials file > env var
            key = self.api_key
            if not key and self.provider_name != "ollama":
                key = self._credentials.resolve_api_key(self.provider_name)

            self._provider = create_provider(
                provider_name=self.provider_name,
                api_key=key,
            )
        return self._provider

    # Backward compatibility alias
    @property
    def client(self) -> Any:
        """Legacy property -- returns the underlying provider's client."""
        return self.provider

    def reset_client(self) -> None:
        """Force re-creation of the provider (e.g. after key/provider change)."""
        self._provider = None

    def _track_usage(self, usage: Any) -> None:
        """Extract and accumulate token usage from LLMResponse."""
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
    ) -> LLMResponse:
        """Send a completion request via the active provider.

        Returns an LLMResponse with the same attribute interface as before:
        response.content, response.usage, block.type/text/name/input/id.
        """
        response = await self.provider.complete(
            messages=messages,
            system=system,
            model=model or self.model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            tools=tools,
            cache=cache,
        )
        self._track_usage(response.usage)
        return response

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
    ) -> LLMResponse:
        """Stream a completion, calling on_text for each text delta.

        Returns the final LLMResponse (same shape as complete()).
        Falls back to complete() if on_text is None.
        """
        response = await self.provider.stream(
            messages=messages,
            system=system,
            model=model or self.model,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
            tools=tools,
            cache=cache,
            on_text=on_text,
        )
        self._track_usage(response.usage)
        return response

    async def complete_text(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        **kwargs: Any,
    ) -> str:
        """Convenience method that returns just the text content."""
        response = await self.complete(messages, system=system, **kwargs)
        return response.content[0].text  # type: ignore[union-attr]
