"""Ollama provider backend.

Reuses the OpenAI provider with a custom base URL pointing to the
local Ollama server. No API key required.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from opentf.llm.providers.openai import OpenAIProvider
from opentf.llm.types import LLMResponse, TextBlock, Usage

log = logging.getLogger(__name__)


class OllamaProvider(OpenAIProvider):
    """Ollama local model provider (OpenAI-compatible API)."""

    def __init__(self, base_url: str = "http://localhost:11434/v1") -> None:
        # Ollama doesn't need a real API key but the SDK requires one
        super().__init__(api_key="ollama", base_url=base_url)

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
        try:
            return await super().complete(
                messages, system=system, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools, cache=cache,
            )
        except Exception as exc:
            # If tool use fails (model doesn't support it), retry without tools
            if tools and "tool" in str(exc).lower():
                log.warning(
                    "Ollama model '%s' may not support tools, retrying without: %s",
                    model, exc,
                )
                return await super().complete(
                    messages, system=system, model=model, max_tokens=max_tokens,
                    temperature=temperature, tools=None, cache=cache,
                )
            raise

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
        try:
            return await super().stream(
                messages, system=system, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools, cache=cache,
                on_text=on_text,
            )
        except Exception as exc:
            if tools and "tool" in str(exc).lower():
                log.warning(
                    "Ollama model '%s' may not support tools in stream, retrying without: %s",
                    model, exc,
                )
                return await super().stream(
                    messages, system=system, model=model, max_tokens=max_tokens,
                    temperature=temperature, tools=None, cache=cache,
                    on_text=on_text,
                )
            raise
