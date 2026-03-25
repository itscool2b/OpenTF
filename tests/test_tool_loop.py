"""Tests for the ToolLoop multi-turn conversation engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from opentf.core.tool_loop import ToolLoop, _truncate_result, _block_to_dict
from opentf.llm.types import LLMResponse, TextBlock, ToolUseBlock, Usage
from opentf.tools.file_tools import ApprovalRequired
from opentf.models.message import MessageType


# --- Helpers ---

def _response(*blocks, inp: int = 10, out: int = 5) -> LLMResponse:
    return LLMResponse(
        content=list(blocks),
        usage=Usage(input_tokens=inp, output_tokens=out),
    )


def _make_llm(*responses: LLMResponse) -> MagicMock:
    """Create a mock LLMClient that returns preset responses in order."""
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=list(responses))
    return llm


# --- _truncate_result tests ---

def test_truncate_short_passthrough() -> None:
    result = _truncate_result("short text")
    assert result == "short text"


def test_truncate_empty() -> None:
    result = _truncate_result("")
    assert result == ""


def test_truncate_long_no_errors() -> None:
    long_text = "a" * 500_000
    result = _truncate_result(long_text)
    assert len(result) < len(long_text)
    assert "truncated" in result.lower() or "omitted" in result.lower()


def test_truncate_long_with_error_tail() -> None:
    head = "x" * 400_000
    tail = "\nTraceback (most recent call last):\n  Error happened"
    long_text = head + tail
    result = _truncate_result(long_text)
    assert len(result) < len(long_text)
    # Should preserve the error tail
    assert "omitted" in result


# --- _block_to_dict tests ---

def test_block_to_dict_text() -> None:
    block = TextBlock(text="hello")
    d = _block_to_dict(block)
    assert d == {"type": "text", "text": "hello"}


def test_block_to_dict_tool_use() -> None:
    block = ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"})
    d = _block_to_dict(block)
    assert d == {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}


def test_block_to_dict_unknown() -> None:
    block = MagicMock()
    block.type = "image"
    d = _block_to_dict(block)
    assert d == {"type": "image"}


# --- Single-turn (no tool calls) ---

@pytest.mark.asyncio
async def test_single_turn_text_only() -> None:
    llm = _make_llm(_response(TextBlock(text="Hello!")))
    loop = ToolLoop(llm, tools=[], handlers={})
    text, tokens = await loop.run([{"role": "user", "content": "hi"}])
    assert text == "Hello!"
    assert tokens == 15


@pytest.mark.asyncio
async def test_single_turn_multiple_text_blocks() -> None:
    llm = _make_llm(_response(TextBlock(text="Part 1"), TextBlock(text="Part 2")))
    loop = ToolLoop(llm, tools=[], handlers={})
    text, tokens = await loop.run([{"role": "user", "content": "hi"}])
    assert text == "Part 1\nPart 2"


@pytest.mark.asyncio
async def test_single_turn_on_stream_called() -> None:
    llm = _make_llm(_response(TextBlock(text="Streamed")))
    stream_cb = AsyncMock()
    loop = ToolLoop(llm, tools=[], handlers={}, on_stream=stream_cb)
    text, _ = await loop.run([{"role": "user", "content": "hi"}])
    assert text == "Streamed"
    stream_cb.assert_called_once_with("Streamed")


# --- Multi-turn (tool calls) ---

@pytest.mark.asyncio
async def test_multi_turn_tool_then_text() -> None:
    tool_resp = _response(
        ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
        inp=20, out=10,
    )
    final_resp = _response(TextBlock(text="Done"), inp=30, out=15)
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(return_value="file contents")
    loop = ToolLoop(llm, tools=[], handlers={"read_file": handler})
    text, tokens = await loop.run([{"role": "user", "content": "read a.py"}])

    assert text == "Done"
    assert tokens == 75  # 20+10 + 30+15
    handler.assert_called_once_with({"path": "a.py"})


@pytest.mark.asyncio
async def test_multi_turn_multiple_tools_in_one_response() -> None:
    tool_resp = _response(
        ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
        ToolUseBlock(id="t2", name="read_file", input={"path": "b.py"}),
    )
    final_resp = _response(TextBlock(text="Both read"))
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(return_value="contents")
    loop = ToolLoop(llm, tools=[], handlers={"read_file": handler})
    text, _ = await loop.run([{"role": "user", "content": "read both"}])

    assert text == "Both read"
    assert handler.call_count == 2


# --- Unknown tool ---

@pytest.mark.asyncio
async def test_unknown_tool_returns_error() -> None:
    tool_resp = _response(
        ToolUseBlock(id="t1", name="nonexistent", input={}),
    )
    final_resp = _response(TextBlock(text="Handled"))
    llm = _make_llm(tool_resp, final_resp)

    loop = ToolLoop(llm, tools=[], handlers={})
    text, _ = await loop.run([{"role": "user", "content": "test"}])
    assert text == "Handled"

    # Verify the error result was sent back to the LLM
    second_call_msgs = llm.complete.call_args_list[1][1]["messages"]
    tool_results = second_call_msgs[-1]["content"]
    assert any("unknown tool" in str(r.get("content", "")).lower() for r in tool_results)


# --- Handler exceptions ---

@pytest.mark.asyncio
async def test_handler_exception_returns_error() -> None:
    tool_resp = _response(ToolUseBlock(id="t1", name="bad_tool", input={}))
    final_resp = _response(TextBlock(text="Recovered"))
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(side_effect=RuntimeError("kaboom"))
    loop = ToolLoop(llm, tools=[], handlers={"bad_tool": handler})
    text, _ = await loop.run([{"role": "user", "content": "test"}])

    assert text == "Recovered"
    second_call_msgs = llm.complete.call_args_list[1][1]["messages"]
    tool_results = second_call_msgs[-1]["content"]
    assert any(r.get("is_error") for r in tool_results)


@pytest.mark.asyncio
async def test_approval_required_no_bus_denied() -> None:
    tool_resp = _response(ToolUseBlock(id="t1", name="danger", input={}))
    final_resp = _response(TextBlock(text="Denied"))
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(side_effect=ApprovalRequired("rm -rf /"))
    loop = ToolLoop(llm, tools=[], handlers={"danger": handler}, bus=None)
    text, _ = await loop.run([{"role": "user", "content": "test"}])

    assert text == "Denied"
    second_call_msgs = llm.complete.call_args_list[1][1]["messages"]
    tool_results = second_call_msgs[-1]["content"]
    assert any("declined" in str(r.get("content", "")).lower() for r in tool_results)


# --- Max iterations ---

@pytest.mark.asyncio
async def test_max_iterations_hit() -> None:
    # LLM always returns tool_use
    tool_resp = _response(ToolUseBlock(id="t1", name="loop_tool", input={}))
    responses = [tool_resp] * 5
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=responses)

    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"loop_tool": handler}, max_iterations=5)
    text, tokens = await loop.run([{"role": "user", "content": "test"}])

    assert "maximum iterations" in text.lower()
    assert tokens > 0


# --- Cancellation ---

@pytest.mark.asyncio
async def test_cancellation_before_first_iteration() -> None:
    llm = _make_llm(_response(TextBlock(text="Should not reach")))
    loop = ToolLoop(llm, tools=[], handlers={}, is_cancelled=lambda: True)
    text, tokens = await loop.run([{"role": "user", "content": "test"}])

    assert "cancelled" in text.lower()
    llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_after_first_iteration() -> None:
    call_count = 0

    def is_cancelled() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 1

    tool_resp = _response(ToolUseBlock(id="t1", name="tool", input={}))
    llm = _make_llm(tool_resp)

    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler}, is_cancelled=is_cancelled)
    text, _ = await loop.run([{"role": "user", "content": "test"}])

    assert "cancelled" in text.lower()


# --- Bus integration ---

@pytest.mark.asyncio
async def test_bus_tool_invoked_published() -> None:
    tool_resp = _response(ToolUseBlock(id="t1", name="read_file", input={"path": "x"}))
    final_resp = _response(TextBlock(text="Done"))
    llm = _make_llm(tool_resp, final_resp)

    bus = MagicMock()
    bus.publish = AsyncMock()
    handler = AsyncMock(return_value="contents")
    loop = ToolLoop(llm, tools=[], handlers={"read_file": handler}, bus=bus)
    await loop.run([{"role": "user", "content": "test"}])

    # Check bus was called with TOOL_INVOKED and TOOL_RESULT
    published_types = [call.args[0].type for call in bus.publish.call_args_list]
    assert MessageType.TOOL_INVOKED in published_types
    assert MessageType.TOOL_RESULT in published_types


@pytest.mark.asyncio
async def test_no_bus_no_crash() -> None:
    tool_resp = _response(ToolUseBlock(id="t1", name="tool", input={}))
    final_resp = _response(TextBlock(text="Done"))
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler}, bus=None)
    text, _ = await loop.run([{"role": "user", "content": "test"}])
    assert text == "Done"


# --- Streaming intermediate text ---

@pytest.mark.asyncio
async def test_intermediate_text_streamed() -> None:
    tool_resp = _response(
        TextBlock(text="Let me check"),
        ToolUseBlock(id="t1", name="tool", input={}),
    )
    final_resp = _response(TextBlock(text="Done"))
    llm = _make_llm(tool_resp, final_resp)

    stream_cb = AsyncMock()
    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler}, on_stream=stream_cb)
    await loop.run([{"role": "user", "content": "test"}])

    # Stream should have been called for intermediate and final
    calls = [c.args[0] for c in stream_cb.call_args_list]
    assert "Let me check" in calls
    assert "Done" in calls


@pytest.mark.asyncio
async def test_empty_intermediate_not_streamed() -> None:
    tool_resp = _response(
        TextBlock(text=""),
        ToolUseBlock(id="t1", name="tool", input={}),
    )
    final_resp = _response(TextBlock(text="Done"))
    llm = _make_llm(tool_resp, final_resp)

    stream_cb = AsyncMock()
    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler}, on_stream=stream_cb)
    await loop.run([{"role": "user", "content": "test"}])

    # Only final text should be streamed
    assert stream_cb.call_count == 1
    stream_cb.assert_called_with("Done")


# --- Token accumulation ---

@pytest.mark.asyncio
async def test_tokens_accumulated_across_turns() -> None:
    tool_resp = _response(
        ToolUseBlock(id="t1", name="tool", input={}),
        inp=100, out=50,
    )
    final_resp = _response(TextBlock(text="Done"), inp=200, out=100)
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(return_value="ok")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler})
    _, tokens = await loop.run([{"role": "user", "content": "test"}])

    assert tokens == 450  # 100+50 + 200+100


# --- Message format ---

@pytest.mark.asyncio
async def test_tool_result_message_format() -> None:
    tool_resp = _response(ToolUseBlock(id="call_123", name="tool", input={}))
    final_resp = _response(TextBlock(text="Done"))
    llm = _make_llm(tool_resp, final_resp)

    handler = AsyncMock(return_value="result_value")
    loop = ToolLoop(llm, tools=[], handlers={"tool": handler})
    await loop.run([{"role": "user", "content": "test"}])

    # Check the tool result message sent back
    second_call_msgs = llm.complete.call_args_list[1][1]["messages"]
    tool_result_msg = second_call_msgs[-1]
    assert tool_result_msg["role"] == "user"
    result_block = tool_result_msg["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "call_123"
    assert "result_value" in result_block["content"]
