"""LLM provider protocol.

All provider backends implement this interface so that LLMClient
can delegate to any provider transparently.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable, Protocol

from opentf.llm.types import LLMResponse


class LLMProvider(Protocol):
    """Protocol that all LLM provider backends must implement."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        cache: bool,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        cache: bool,
        on_text: Callable[[str], Awaitable[None]] | None,
    ) -> LLMResponse: ...
