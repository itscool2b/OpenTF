"""Tests for LLM provider types, message conversion, and factory."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from opentf.llm.types import TextBlock, ToolUseBlock, Usage, LLMResponse
from opentf.llm.providers import create_provider
from opentf.llm.providers.openai import _convert_tools, _convert_messages


# --- LLMResponse type tests ---

def test_text_block() -> None:
    block = TextBlock(text="hello")
    assert block.type == "text"
    assert block.text == "hello"


def test_tool_use_block() -> None:
    block = ToolUseBlock(id="123", name="read_file", input={"path": "/tmp"})
    assert block.type == "tool_use"
    assert block.id == "123"
    assert block.name == "read_file"
    assert block.input == {"path": "/tmp"}


def test_usage() -> None:
    usage = Usage(input_tokens=100, output_tokens=50)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0


def test_llm_response_text_access() -> None:
    """Verify the exact attribute access pattern from tool_loop.py."""
    response = LLMResponse(
        content=[TextBlock(text="Hello world")],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    # This is the pattern in complete_text()
    assert response.content[0].text == "Hello world"
    # This is the pattern in tool_loop.py line 121
    assert response.usage.input_tokens + response.usage.output_tokens == 15


def test_llm_response_tool_use_access() -> None:
    """Verify tool_use block access pattern from tool_loop.py lines 123-159."""
    response = LLMResponse(
        content=[
            TextBlock(text="Let me read that file."),
            ToolUseBlock(id="call_1", name="read_file", input={"path": "test.py"}),
        ],
        usage=Usage(input_tokens=50, output_tokens=30),
    )
    # Pattern from tool_loop.py lines 123-126
    tool_use_blocks = [
        block for block in response.content
        if block.type == "tool_use"
    ]
    assert len(tool_use_blocks) == 1

    text_blocks = [
        block for block in response.content
        if block.type == "text"
    ]
    assert len(text_blocks) == 1

    # Pattern from tool_loop.py lines 156-159
    block = tool_use_blocks[0]
    assert block.name == "read_file"
    assert block.input == {"path": "test.py"}
    assert block.id == "call_1"


# --- OpenAI tool conversion tests ---

def test_convert_tools_anthropic_to_openai() -> None:
    anthropic_tools = [
        {
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    openai_tools = _convert_tools(anthropic_tools)
    assert len(openai_tools) == 1
    assert openai_tools[0]["type"] == "function"
    assert openai_tools[0]["function"]["name"] == "read_file"
    assert openai_tools[0]["function"]["parameters"]["type"] == "object"
    assert "path" in openai_tools[0]["function"]["parameters"]["properties"]


def test_convert_tools_strips_cache_control() -> None:
    tools = [
        {
            "name": "test",
            "description": "test",
            "input_schema": {
                "type": "object",
                "properties": {},
                "cache_control": {"type": "ephemeral"},
            },
        }
    ]
    result = _convert_tools(tools)
    assert "cache_control" not in result[0]["function"]["parameters"]


# --- OpenAI message conversion tests ---

def test_convert_simple_messages() -> None:
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    result = _convert_messages(messages)
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "Hello"}
    assert result[1] == {"role": "assistant", "content": "Hi there"}


def test_convert_tool_use_in_assistant() -> None:
    """Anthropic tool_use blocks -> OpenAI tool_calls."""
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "test.py"},
                },
            ],
        }
    ]
    result = _convert_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Let me check."
    assert len(result[0]["tool_calls"]) == 1
    tc = result[0]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "read_file"
    assert json.loads(tc["function"]["arguments"]) == {"path": "test.py"}


def test_convert_tool_result_in_user() -> None:
    """Anthropic tool_result -> OpenAI role=tool messages."""
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": "file contents here",
                },
            ],
        }
    ]
    result = _convert_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "tool"
    assert result[0]["tool_call_id"] == "call_1"
    assert result[0]["content"] == "file contents here"


# --- Factory tests ---

def test_create_provider_anthropic() -> None:
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test-key123456789012"}):
        provider = create_provider("anthropic", api_key="sk-ant-test-key")
        assert provider is not None


def test_create_provider_openai() -> None:
    provider = create_provider("openai", api_key="sk-test-key")
    assert provider is not None


def test_create_provider_ollama() -> None:
    provider = create_provider("ollama")
    assert provider is not None


def test_create_provider_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        create_provider("unknown_provider")
