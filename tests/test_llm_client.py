"""Tests for LLMClient facade and TokenUsage tracking."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from opentf.llm.client import LLMClient, TokenUsage
from opentf.llm.types import LLMResponse, TextBlock, ToolUseBlock, Usage


# --- TokenUsage ---

def test_token_usage_defaults() -> None:
    u = TokenUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.cache_write_tokens == 0
    assert u.cache_read_tokens == 0


def test_token_usage_total() -> None:
    u = TokenUsage(input_tokens=100, output_tokens=50)
    assert u.total == 150


def test_token_usage_add() -> None:
    u = TokenUsage()
    u.add(100, 50, cache_write=10, cache_read=5)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.cache_write_tokens == 10
    assert u.cache_read_tokens == 5
    u.add(200, 100)
    assert u.input_tokens == 300
    assert u.output_tokens == 150
    assert u.cache_write_tokens == 10


# --- LLMClient defaults ---

def test_client_defaults() -> None:
    client = LLMClient()
    assert client.provider_name == "anthropic"
    assert "sonnet" in client.model
    assert client.max_tokens == 8192
    assert client.temperature == 0.7


def test_client_custom_values() -> None:
    client = LLMClient(provider_name="openai", model="gpt-4o", max_tokens=4096, temperature=0.5)
    assert client.provider_name == "openai"
    assert client.model == "gpt-4o"
    assert client.max_tokens == 4096
    assert client.temperature == 0.5


# --- Provider lazy creation ---

def test_provider_initially_none() -> None:
    client = LLMClient()
    assert client._provider is None


def test_provider_created_on_access() -> None:
    mock_provider = MagicMock()
    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(provider_name="anthropic", api_key="test-key")
        provider = client.provider
        assert provider is mock_provider


def test_provider_cached_on_second_access() -> None:
    mock_provider = MagicMock()
    with patch("opentf.llm.providers.create_provider", return_value=mock_provider) as factory:
        client = LLMClient(provider_name="anthropic", api_key="test-key")
        _ = client.provider
        _ = client.provider
        factory.assert_called_once()


def test_reset_client_clears_provider() -> None:
    mock_provider = MagicMock()
    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(provider_name="anthropic", api_key="test-key")
        _ = client.provider
        assert client._provider is not None
        client.reset_client()
        assert client._provider is None


# --- complete() delegation ---

@pytest.mark.asyncio
async def test_complete_delegates_to_provider() -> None:
    mock_response = LLMResponse(
        content=[TextBlock(text="Hello")],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key")
        result = await client.complete([{"role": "user", "content": "hi"}])

        assert result.content[0].text == "Hello"
        mock_provider.complete.assert_called_once()


@pytest.mark.asyncio
async def test_complete_tracks_usage() -> None:
    mock_response = LLMResponse(
        content=[TextBlock(text="Hi")],
        usage=Usage(input_tokens=100, output_tokens=50, cache_creation_input_tokens=20, cache_read_input_tokens=10),
    )
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key")
        await client.complete([{"role": "user", "content": "hi"}])

        assert client.usage.input_tokens == 100
        assert client.usage.output_tokens == 50
        assert client.usage.cache_write_tokens == 20
        assert client.usage.cache_read_tokens == 10


@pytest.mark.asyncio
async def test_complete_uses_default_params() -> None:
    mock_response = LLMResponse(content=[TextBlock(text="Hi")], usage=Usage())
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key", model="test-model", max_tokens=2048, temperature=0.3)
        await client.complete([{"role": "user", "content": "hi"}])

        call_kwargs = mock_provider.complete.call_args[1]
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["max_tokens"] == 2048
        assert call_kwargs["temperature"] == 0.3


@pytest.mark.asyncio
async def test_complete_overrides_defaults() -> None:
    mock_response = LLMResponse(content=[TextBlock(text="Hi")], usage=Usage())
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key", model="default-model")
        await client.complete(
            [{"role": "user", "content": "hi"}],
            model="override-model",
            max_tokens=1024,
            temperature=0.9,
        )

        call_kwargs = mock_provider.complete.call_args[1]
        assert call_kwargs["model"] == "override-model"
        assert call_kwargs["max_tokens"] == 1024
        assert call_kwargs["temperature"] == 0.9


# --- stream() delegation ---

@pytest.mark.asyncio
async def test_stream_delegates_to_provider() -> None:
    mock_response = LLMResponse(
        content=[TextBlock(text="Streamed")],
        usage=Usage(input_tokens=20, output_tokens=10),
    )
    mock_provider = MagicMock()
    mock_provider.stream = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key")
        result = await client.stream([{"role": "user", "content": "hi"}])

        assert result.content[0].text == "Streamed"
        assert client.usage.input_tokens == 20


# --- complete_text() convenience ---

@pytest.mark.asyncio
async def test_complete_text_returns_string() -> None:
    mock_response = LLMResponse(
        content=[TextBlock(text="Just text")],
        usage=Usage(input_tokens=5, output_tokens=3),
    )
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=mock_response)

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key")
        text = await client.complete_text([{"role": "user", "content": "hi"}])

        assert text == "Just text"
        assert client.usage.total == 8


# --- Multiple calls accumulate usage ---

@pytest.mark.asyncio
async def test_usage_accumulates_across_calls() -> None:
    resp1 = LLMResponse(content=[TextBlock(text="a")], usage=Usage(input_tokens=10, output_tokens=5))
    resp2 = LLMResponse(content=[TextBlock(text="b")], usage=Usage(input_tokens=20, output_tokens=10))
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=[resp1, resp2])

    with patch("opentf.llm.providers.create_provider", return_value=mock_provider):
        client = LLMClient(api_key="test-key")
        await client.complete([{"role": "user", "content": "1"}])
        await client.complete([{"role": "user", "content": "2"}])

        assert client.usage.input_tokens == 30
        assert client.usage.output_tokens == 15
        assert client.usage.total == 45
