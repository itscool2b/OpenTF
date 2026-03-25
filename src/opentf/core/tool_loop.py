"""Shared tool_use conversation loop for specialist agents.

Handles the multi-turn cycle: send tools to LLM, execute tool calls,
send results back, repeat until LLM produces a final text response.
Publishes bus messages for every tool invocation when bus is provided.
Uses prompt caching for system+tools across iterations.
"""

from __future__ import annotations

import asyncio
import re
import logging
from typing import Any, Callable, Awaitable

from opentf.llm.client import LLMClient
from opentf.models.message import Message, MessageType
from opentf.tools.file_tools import ApprovalRequired

log = logging.getLogger(__name__)

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]

# --- Adaptive truncation config ---

CONTEXT_WINDOW_TOKENS = 200_000
CHARS_PER_TOKEN = 4
MIN_RESULT_CHARS = 2000
MAX_RESULT_FRACTION = 0.30
MAX_RESULT_CHARS_CAP = 400_000
ERROR_TAIL_PATTERN = re.compile(
    r"error|exception|failed|traceback|panic|fatal|errno|exit code",
    re.IGNORECASE,
)


def _truncate_result(result_str: str) -> str:
    """Adaptively truncate a tool result.

    Uses head+tail preservation when errors are detected in the tail.
    """
    max_chars = min(
        int(CONTEXT_WINDOW_TOKENS * MAX_RESULT_FRACTION * CHARS_PER_TOKEN),
        MAX_RESULT_CHARS_CAP,
    )
    max_chars = max(max_chars, MIN_RESULT_CHARS)

    if len(result_str) <= max_chars:
        return result_str

    tail_sample = result_str[-min(len(result_str), 2000):]
    has_errors = bool(ERROR_TAIL_PATTERN.search(tail_sample))

    if has_errors:
        head_chars = int(max_chars * 0.70)
        tail_chars = max_chars - head_chars
        omitted = len(result_str) - head_chars - tail_chars
        return (
            result_str[:head_chars]
            + f"\n\n[... {omitted:,} chars omitted ...]\n\n"
            + result_str[-tail_chars:]
        )
    else:
        omitted = len(result_str) - max_chars
        return result_str[:max_chars] + f"\n... (truncated, {omitted:,} chars omitted)"


class ToolLoop:
    """Executes an LLM tool_use conversation loop.

    Each specialist defines its own tool definitions and async handler
    functions, then passes them here. ToolLoop manages the back-and-forth.
    When bus is provided, publishes TOOL_INVOKED/TOOL_RESULT for live logging.
    Uses prompt caching to reduce token costs across iterations.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: list[dict[str, Any]],
        handlers: dict[str, ToolHandler],
        max_iterations: int = 10,
        bus: Any | None = None,
        source: str = "agent",
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.handlers = handlers
        self.max_iterations = max_iterations
        self.bus = bus
        self.source = source
        self.on_stream = on_stream
        self.is_cancelled = is_cancelled

    async def run(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        temperature: float = 0.3,
    ) -> tuple[str, int]:
        """Run the tool loop.

        Returns (final_text, total_tokens).
        """
        total_tokens = 0
        msgs = list(messages)

        for iteration in range(self.max_iterations):
            if self.is_cancelled and self.is_cancelled():
                return "(Cancelled by user)", total_tokens

            response = await self.llm.complete(
                messages=msgs,
                system=system,
                temperature=temperature,
                tools=self.tools,
                cache=True,
            )
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            tool_use_blocks = [
                block for block in response.content
                if block.type == "tool_use"
            ]

            if not tool_use_blocks:
                text_parts = [
                    block.text for block in response.content
                    if block.type == "text"
                ]
                final_text = "\n".join(text_parts)
                # Emit final text to stream callback if set
                if self.on_stream and final_text:
                    await self.on_stream(final_text)
                return final_text, total_tokens

            # Stream intermediate text (LLM reasoning before tool calls)
            if self.on_stream:
                text_parts = [b.text for b in response.content if b.type == "text"]
                intermediate = "\n".join(text_parts)
                if intermediate.strip():
                    await self.on_stream(intermediate)

            # Append assistant response to messages
            msgs.append({
                "role": "assistant",
                "content": [
                    _block_to_dict(block) for block in response.content
                ],
            })

            # Execute each tool
            tool_results = []
            for block in tool_use_blocks:
                tool_name = block.name
                tool_input = block.input
                tool_id = block.id

                # Publish tool invocation
                if self.bus:
                    input_summary = str(tool_input)[:120]
                    await self.bus.publish(Message(
                        type=MessageType.TOOL_INVOKED,
                        source=self.source,
                        payload={"tool": tool_name, "input_summary": input_summary},
                    ))

                handler = self.handlers.get(tool_name)
                if handler is None:
                    log.warning("No handler for tool: %s", tool_name)
                    if self.bus:
                        await self.bus.publish(Message(
                            type=MessageType.TOOL_RESULT,
                            source=self.source,
                            payload={"tool": tool_name, "success": False, "summary": "unknown tool"},
                        ))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": f"Error: unknown tool '{tool_name}'",
                        "is_error": True,
                    })
                    continue

                try:
                    result = await handler(tool_input)
                    result_str = _truncate_result(str(result))

                    if self.bus:
                        await self.bus.publish(Message(
                            type=MessageType.TOOL_RESULT,
                            source=self.source,
                            payload={"tool": tool_name, "success": True, "summary": result_str[:100]},
                        ))

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_str,
                    })
                except ApprovalRequired as req:
                    approved = await self._request_approval(
                        req.command, tool_id, diff_text=req.diff_text,
                    )
                    if approved:
                        # Re-call handler with approval flag
                        try:
                            approved_input = {**tool_input, "_approved": True}
                            result = await handler(approved_input)
                            result_str = _truncate_result(str(result))
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_str,
                            })
                        except Exception as exc2:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": f"Error: {exc2}",
                                "is_error": True,
                            })
                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": f"Error: user declined: {req.command}",
                            "is_error": True,
                        })
                except Exception as exc:
                    log.error("Tool %s failed: %s", tool_name, exc)

                    if self.bus:
                        await self.bus.publish(Message(
                            type=MessageType.TOOL_RESULT,
                            source=self.source,
                            payload={"tool": tool_name, "success": False, "summary": str(exc)[:100]},
                        ))

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": f"Error: {exc}",
                        "is_error": True,
                    })

            msgs.append({"role": "user", "content": tool_results})

        log.warning("ToolLoop hit max iterations (%d)", self.max_iterations)
        return "(Tool loop reached maximum iterations)", total_tokens

    async def _request_approval(self, command: str, tool_id: str, diff_text: str | None = None) -> bool:
        """Request user approval for a command via bus.

        User can respond: yes (once), no (skip), always (session-wide allow).
        """
        if not self.bus:
            log.warning("No bus for approval request, denying: %s", command)
            return False

        approval_event = asyncio.Event()
        approved = False

        async def on_granted(msg: Message) -> None:
            nonlocal approved
            if msg.payload.get("tool_id") == tool_id:
                approved = True
                approval_event.set()

        async def on_denied(msg: Message) -> None:
            if msg.payload.get("tool_id") == tool_id:
                approval_event.set()

        async def on_always(msg: Message) -> None:
            nonlocal approved
            if msg.payload.get("tool_id") == tool_id:
                approved = True
                from opentf.tools.file_tools import add_to_session_allowlist
                add_to_session_allowlist(command)
                approval_event.set()

        self.bus.subscribe(MessageType.APPROVAL_GRANTED, on_granted)
        self.bus.subscribe(MessageType.APPROVAL_DENIED, on_denied)
        self.bus.subscribe(MessageType.APPROVAL_ALWAYS, on_always)

        await self.bus.publish(Message(
            type=MessageType.APPROVAL_REQUESTED,
            source=self.source,
            payload={"command": command, "tool_id": tool_id, "diff_text": diff_text},
        ))

        try:
            await approval_event.wait()
        finally:
            self.bus.unsubscribe(MessageType.APPROVAL_GRANTED, on_granted)
            self.bus.unsubscribe(MessageType.APPROVAL_DENIED, on_denied)
            self.bus.unsubscribe(MessageType.APPROVAL_ALWAYS, on_always)

        return approved


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Convert an Anthropic content block to a dict for message history."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    elif block.type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    return {"type": block.type}
