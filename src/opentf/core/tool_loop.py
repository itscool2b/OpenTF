"""Shared tool_use conversation loop for specialist agents.

Handles the multi-turn cycle: send tools to LLM, execute tool calls,
send results back, repeat until LLM produces a final text response.
Publishes bus messages for every tool invocation when bus is provided.
Uses prompt caching for system+tools across iterations.

Smart termination features:
- Three-tier limits: soft (nudge), stuck detection, hard (force)
- Token budget tracking to prevent context overflow
- Progress bus messages for UI consumption
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import logging
from dataclasses import dataclass, field
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

# Default config
DEFAULT_MAX_ITERATIONS = 30
DEFAULT_TOKEN_BUDGET = 150_000
DEFAULT_STUCK_WINDOW = 3
DEFAULT_SOFT_LIMIT_PCT = 0.80

# Tools safe for parallel execution (read-only, no side effects)
PARALLEL_SAFE_TOOLS: set[str] = {
    "read_file", "list_directory", "search_files", "grep", "glob",
    "git_status", "git_diff", "git_log", "git_show", "git_branch",
    "read_data", "describe_data",
    "web_search", "read_url",
}


@dataclass
class IterationRecord:
    """Tracks what happened in a single tool loop iteration."""
    iteration: int
    tools_called: list[str] = field(default_factory=list)
    input_hashes: list[str] = field(default_factory=list)
    tokens_used: int = 0


MAX_RESULT_LINES = 2000

# Patterns worth preserving in the middle of truncated output
_PRESERVE_PATTERN = re.compile(
    r"error|exception|failed|traceback|panic|fatal|errno|exit code|"
    r"warning|warn|FAIL|PASSED|assert|===",
    re.IGNORECASE,
)


def _truncate_result(result_str: str) -> str:
    """Adaptively truncate a tool result.

    Two-pass: first line-based truncation (preserving important lines
    from the middle), then char-based truncation with head+tail preservation
    when errors are detected.
    """
    # Pass 1: Line-based truncation (keeps important middle lines)
    lines = result_str.splitlines(keepends=True)
    if len(lines) > MAX_RESULT_LINES:
        head_n = int(MAX_RESULT_LINES * 0.30)
        tail_n = int(MAX_RESULT_LINES * 0.40)
        middle = lines[head_n:-tail_n]
        important = [l for l in middle if _PRESERVE_PATTERN.search(l)]
        budget = MAX_RESULT_LINES - head_n - tail_n
        kept_middle = important[:budget]
        omitted = len(lines) - head_n - tail_n - len(kept_middle)
        result_str = "".join(
            lines[:head_n]
            + [f"\n[... {omitted} lines omitted ...]\n"]
            + kept_middle
            + lines[-tail_n:]
        )

    # Pass 2: Char-based truncation
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


def _hash_tool_call(name: str, input_data: dict) -> str:
    """Create a fingerprint of a tool call for stuck detection."""
    raw = json.dumps({"n": name, "i": input_data}, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


class ToolLoop:
    """Executes an LLM tool_use conversation loop with smart termination.

    Features:
    - Soft limit at 80%: nudges the LLM to wrap up
    - Stuck detection: identifies repeated identical tool calls
    - Token budget: prevents context window overflow
    - Progress bus messages for UI
    - Auto-test hook: runs tests after edits and feeds failures back
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: list[dict[str, Any]],
        handlers: dict[str, ToolHandler],
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        bus: Any | None = None,
        source: str = "agent",
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        stuck_window: int = DEFAULT_STUCK_WINDOW,
        soft_limit_pct: float = DEFAULT_SOFT_LIMIT_PCT,
        post_edit_hook: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.handlers = handlers
        self.max_iterations = max_iterations
        self.token_budget = token_budget
        self.bus = bus
        self.source = source
        self.on_stream = on_stream
        self.is_cancelled = is_cancelled
        self.stuck_window = stuck_window
        self.soft_limit_pct = soft_limit_pct

        self.post_edit_hook = post_edit_hook

        self._history: list[IterationRecord] = []
        self._edits_this_session: int = 0
        self._auto_test_ran: bool = False
        self.parallel: bool = True

    async def run(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        temperature: float = 0.3,
    ) -> tuple[str, int]:
        """Run the tool loop. Returns (final_text, total_tokens)."""
        total_tokens = 0
        msgs = list(messages)
        soft_limit = int(self.max_iterations * self.soft_limit_pct)
        soft_limit_sent = False

        for iteration in range(self.max_iterations):
            if self.is_cancelled and self.is_cancelled():
                return "(Cancelled by user)", total_tokens

            # --- Smart termination checks ---

            # Token budget check
            if total_tokens > self.token_budget:
                # Give one final wrap-up iteration
                msgs.append({
                    "role": "user",
                    "content": (
                        "You have exceeded the token budget. Please provide your "
                        "final response now, summarizing what you've accomplished."
                    ),
                })
                response = await self.llm.complete(
                    messages=msgs, system=system, temperature=temperature,
                    tools=[], cache=True,
                )
                total_tokens += response.usage.input_tokens + response.usage.output_tokens
                text_parts = [b.text for b in response.content if b.type == "text"]
                return "\n".join(text_parts), total_tokens

            # Stuck detection
            if self._is_stuck():
                msgs.append({
                    "role": "user",
                    "content": (
                        "You appear to be repeating the same action. "
                        "Try a different approach or report what's blocking you."
                    ),
                })

            # Soft limit nudge
            if iteration >= soft_limit and not soft_limit_sent:
                soft_limit_sent = True
                msgs.append({
                    "role": "user",
                    "content": (
                        "You're approaching the iteration limit. "
                        "Focus on completing the current task."
                    ),
                })

            # --- LLM call ---

            # Use streaming if callback set, otherwise complete
            if self.on_stream:
                response = await self.llm.stream(
                    messages=msgs, system=system, temperature=temperature,
                    tools=self.tools, cache=True, on_text=self.on_stream,
                )
            else:
                response = await self.llm.complete(
                    messages=msgs, system=system, temperature=temperature,
                    tools=self.tools, cache=True,
                )
            iter_tokens = response.usage.input_tokens + response.usage.output_tokens
            total_tokens += iter_tokens

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
                if self.on_stream and final_text:
                    await self.on_stream(final_text)

                # Auto-test hook: if edits were made, run tests once
                if (
                    self.post_edit_hook
                    and self._edits_this_session > 0
                    and not self._auto_test_ran
                ):
                    self._auto_test_ran = True
                    try:
                        test_output = await self.post_edit_hook()
                        if test_output:
                            # Tests failed -- feed back for one more iteration
                            msgs.append({
                                "role": "assistant",
                                "content": [{"type": "text", "text": final_text}],
                            })
                            msgs.append({
                                "role": "user",
                                "content": (
                                    f"Auto-test detected failures after your edits. "
                                    f"Please fix the issues:\n\n{test_output}"
                                ),
                            })
                            continue  # One more iteration to fix
                    except Exception as exc:
                        log.warning("Auto-test hook failed: %s", exc)

                return final_text, total_tokens

            # Stream intermediate text (reasoning before tool calls) -- only if not already streaming
            if not self.on_stream:
                pass  # Streaming handled by llm.stream() above
            # For non-streaming mode, intermediate text is in response.content

            # Append assistant response to messages
            msgs.append({
                "role": "assistant",
                "content": [
                    _block_to_dict(block) for block in response.content
                ],
            })

            # --- Execute tools (parallel for reads, sequential for writes) ---
            record = IterationRecord(iteration=iteration, tokens_used=iter_tokens)
            tool_results: list[dict] = []

            # Record metadata for all blocks
            for block in tool_use_blocks:
                record.tools_called.append(block.name)
                record.input_hashes.append(_hash_tool_call(block.name, block.input))
                if block.name in ("edit_file", "write_file", "apply_diff"):
                    self._edits_this_session += 1

            # Partition into parallel-safe batches
            if self.parallel and len(tool_use_blocks) > 1:
                batches = self._partition_for_parallel(tool_use_blocks)
            else:
                batches = [[b] for b in tool_use_blocks]

            for batch in batches:
                if len(batch) > 1:
                    # Execute read-only tools in parallel
                    results = await asyncio.gather(
                        *[self._execute_one_tool(b) for b in batch]
                    )
                    tool_results.extend(results)
                else:
                    # Execute sequentially (single tool or write tool)
                    result = await self._execute_one_tool(batch[0])
                    tool_results.append(result)

            self._history.append(record)
            msgs.append({"role": "user", "content": tool_results})

            # Auto-compaction safety net at 95% context window
            est_context_chars = sum(len(str(m.get("content", ""))) for m in msgs)
            est_context_tokens = est_context_chars // CHARS_PER_TOKEN
            if est_context_tokens > int(CONTEXT_WINDOW_TOKENS * 0.95):
                try:
                    from opentf.core.compaction import compact_history
                    keep = min(6, max(2, len(msgs) // 3))
                    compacted = await compact_history(self.llm, msgs, keep_recent=keep)
                    msgs.clear()
                    msgs.extend(compacted)
                    log.warning(
                        "Auto-compacted at ~%d tokens (95%% of %d)",
                        est_context_tokens, CONTEXT_WINDOW_TOKENS,
                    )
                except Exception as exc:
                    log.warning("Auto-compaction failed: %s", exc)

            # Publish progress
            if self.bus:
                try:
                    await self.bus.publish(Message(
                        type=MessageType.TOOL_INVOKED,
                        source=self.source,
                        payload={
                            "tool": "_progress",
                            "input_summary": (
                                f"iteration {iteration + 1}/{self.max_iterations}, "
                                f"{total_tokens} tokens, "
                                f"{len(record.tools_called)} tools"
                            ),
                        },
                    ))
                except Exception:
                    pass

        log.warning("ToolLoop hit max iterations (%d)", self.max_iterations)
        return "(Tool loop reached maximum iterations)", total_tokens

    async def _execute_one_tool(self, block: Any) -> dict:
        """Execute a single tool call and return a tool_result dict."""
        tool_name = block.name
        tool_input = block.input
        tool_id = block.id

        # Publish invocation
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
            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error: unknown tool '{tool_name}'",
                "is_error": True,
            }

        try:
            result = await handler(tool_input)
            result_str = _truncate_result(str(result))

            if self.bus:
                await self.bus.publish(Message(
                    type=MessageType.TOOL_RESULT,
                    source=self.source,
                    payload={"tool": tool_name, "success": True, "summary": result_str[:100]},
                ))

            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result_str,
            }
        except ApprovalRequired as req:
            approved = await self._request_approval(
                req.command, tool_id, diff_text=req.diff_text,
            )
            if approved:
                try:
                    approved_input = {**tool_input, "_approved": True}
                    result = await handler(approved_input)
                    result_str = _truncate_result(str(result))
                    return {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_str,
                    }
                except Exception as exc2:
                    return {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": f"Error: {exc2}",
                        "is_error": True,
                    }
            else:
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": f"Error: user declined: {req.command}",
                    "is_error": True,
                }
        except Exception as exc:
            log.error("Tool %s failed: %s", tool_name, exc)

            if self.bus:
                await self.bus.publish(Message(
                    type=MessageType.TOOL_RESULT,
                    source=self.source,
                    payload={"tool": tool_name, "success": False, "summary": str(exc)[:100]},
                ))

            return {
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": f"Error: {exc}",
                "is_error": True,
            }

    @staticmethod
    def _partition_for_parallel(blocks: list[Any]) -> list[list[Any]]:
        """Partition tool blocks into batches for parallel execution.

        Consecutive read-only (PARALLEL_SAFE_TOOLS) tools are grouped together.
        Any write tool forces a new sequential batch.
        """
        batches: list[list[Any]] = []
        current_batch: list[Any] = []
        current_is_parallel = True

        for block in blocks:
            is_safe = block.name in PARALLEL_SAFE_TOOLS
            if is_safe and current_is_parallel:
                current_batch.append(block)
            else:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [block]
                current_is_parallel = is_safe

        if current_batch:
            batches.append(current_batch)

        return batches

    def _is_stuck(self) -> bool:
        """Check if the last N iterations show repetitive behavior.

        Detects both exact repetition and near-repetition (same tools
        with slightly different inputs).
        """
        window = max(self.stuck_window, 5)  # Use at least 5 for better detection
        if len(self._history) < window:
            return False
        recent = self._history[-window:]
        if not all(r.input_hashes for r in recent):
            return False

        # Check 1: Exact repetition (identical hash sets)
        first_hashes = tuple(sorted(recent[0].input_hashes))
        exact_match = all(
            tuple(sorted(r.input_hashes)) == first_hashes
            for r in recent[1:]
        )
        if exact_match:
            return True

        # Check 2: Same tool names repeating (even with different inputs)
        first_tools = tuple(sorted(recent[0].tools_called))
        same_tools = all(
            tuple(sorted(r.tools_called)) == first_tools
            for r in recent[1:]
        )
        if same_tools and len(first_tools) > 0:
            # Same tools called every iteration -- likely stuck with variations
            log.info("Stuck detection: same tools %s called %d times", first_tools, window)
            return True

        return False

    @property
    def edits_this_session(self) -> int:
        """Number of edit/write operations performed in this loop run."""
        return self._edits_this_session

    async def _request_approval(self, command: str, tool_id: str, diff_text: str | None = None) -> bool:
        """Request user approval for a command via bus."""
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
            await asyncio.wait_for(approval_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            log.warning("Approval timeout for: %s (auto-denied after 30s)", command)
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
