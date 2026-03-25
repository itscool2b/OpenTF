"""LLM provider factory.

Creates the appropriate provider backend based on the provider name.
"""

from __future__ import annotations

from typing import Any

from opentf.llm.provider import LLMProvider


def create_provider(
    provider_name: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    """Create an LLM provider backend.

    Args:
        provider_name: One of 'anthropic', 'openai', 'ollama'.
        api_key: API key (optional, providers fall back to env vars).
        base_url: Custom API base URL (optional).

    Returns:
        An LLMProvider implementation.

    Raises:
        ValueError: If provider_name is unknown.
    """
    if provider_name == "anthropic":
        from opentf.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=api_key)

    if provider_name == "openai":
        from opentf.llm.providers.openai import OpenAIProvider
        return OpenAIProvider(api_key=api_key, base_url=base_url)

    if provider_name == "ollama":
        from opentf.llm.providers.ollama import OllamaProvider
        return OllamaProvider(base_url=base_url or "http://localhost:11434/v1")

    raise ValueError(
        f"Unknown LLM provider: '{provider_name}'. "
        f"Supported: anthropic, openai, ollama"
    )
