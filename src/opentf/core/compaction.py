"""Smart conversation compaction with file change preservation.

Uses chunked summarization instead of single-pass lossy compression.
Always preserves file modification events verbatim. Iterates until
under the target token count.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

COMPACTION_PROMPT = """\
Summarize this conversation concisely. You MUST preserve:
- ALL file paths that were created, modified, or deleted (exact paths)
- ALL error messages and their resolutions
- Key decisions made and why
- User preferences and corrections
- Technical context (languages, frameworks, project structure)
- Current state of work in progress

Output a brief summary. No preamble."""

# Patterns that indicate file modification events in tool results
_FILE_CHANGE_PATTERNS = re.compile(
    r"(Edited|Written|Created|Removed|Applied)\s+\S+",
    re.IGNORECASE,
)

DEFAULT_KEEP_RECENT = 6
DEFAULT_CHUNK_SIZE = 8


async def compact_history(
    llm: Any,
    history: list[dict[str, Any]],
    keep_recent: int = DEFAULT_KEEP_RECENT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    target_tokens: int | None = None,
) -> list[dict[str, Any]]:
    """Summarize older conversation history with file change preservation.

    Strategy:
    1. Extract file modification events (always preserved verbatim)
    2. Chunk older messages into groups and summarize each independently
    3. Keep recent N messages verbatim
    4. If target_tokens specified, iterate until under target

    Returns:
        Compacted history: [file_changes] + [chunk_summaries] + [recent messages]
    """
    if len(history) <= keep_recent + 1:
        return history

    to_compact = history[:-keep_recent]
    to_keep = history[-keep_recent:]

    # Step 1: Extract file change events
    file_changes = _extract_file_changes(to_compact)

    # Step 2: Chunked summarization
    chunks = _chunk_messages(to_compact, chunk_size)
    summaries: list[str] = []

    for chunk in chunks:
        try:
            summary_text = await llm.complete_text(
                messages=[{"role": "user", "content": _format_for_summary(chunk)}],
                system=COMPACTION_PROMPT,
                temperature=0.3,
            )
            summaries.append(summary_text)
        except Exception as exc:
            log.warning("Chunk compaction failed: %s", exc)
            # Fallback: use truncated raw messages for this chunk
            summaries.append(_format_for_summary(chunk)[:300])

    # Build compacted history
    compacted: list[dict[str, Any]] = []

    # File changes summary (always preserved)
    if file_changes:
        compacted.append({
            "role": "user",
            "content": f"[File changes from prior conversation]\n{chr(10).join(file_changes)}",
        })

    # Chunk summaries
    combined_summary = "\n\n".join(summaries)
    compacted.append({
        "role": "user",
        "content": f"[Prior conversation summary]\n{combined_summary}",
    })

    compacted.extend(to_keep)

    # Step 4: If target_tokens specified, iterate until under target
    if target_tokens is not None:
        for _ in range(3):  # Max 3 re-compaction passes
            est_tokens = sum(len(str(m.get("content", ""))) // 4 for m in compacted)
            if est_tokens <= target_tokens:
                break
            # Re-compact with fewer recent messages preserved
            reduced_keep = max(2, keep_recent - 2)
            if len(compacted) <= reduced_keep + 1:
                break  # Can't compact further
            compacted = await compact_history(
                llm, compacted, keep_recent=reduced_keep,
                chunk_size=chunk_size, target_tokens=None,
            )

    log.info(
        "Compacted %d messages into %d summaries + %d file changes + %d recent",
        len(to_compact), len(summaries), len(file_changes), len(to_keep),
    )

    return compacted


def _extract_file_changes(messages: list[dict[str, Any]]) -> list[str]:
    """Extract file modification events from message history."""
    changes: list[str] = []
    seen: set[str] = set()

    for msg in messages:
        content = str(msg.get("content", ""))
        # Look in tool_result blocks (list content) and plain text
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict):
                    text = block.get("content", "")
                    _extract_from_text(text, changes, seen)
        else:
            _extract_from_text(content, changes, seen)

    return changes


def _extract_from_text(text: str, changes: list[str], seen: set[str]) -> None:
    """Find file change patterns in text and add unique ones to changes."""
    for match in _FILE_CHANGE_PATTERNS.finditer(str(text)):
        line = match.group(0).strip()
        if line not in seen:
            seen.add(line)
            changes.append(f"  - {line}")


def _chunk_messages(
    messages: list[dict[str, Any]], chunk_size: int
) -> list[list[dict[str, Any]]]:
    """Split messages into chunks of chunk_size."""
    chunks: list[list[dict[str, Any]]] = []
    for i in range(0, len(messages), chunk_size):
        chunks.append(messages[i : i + chunk_size])
    return chunks


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Format messages into a readable block for the summarizer."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))
        # Truncate very long messages but keep more than before
        if len(content) > 800:
            content = content[:800] + "..."
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


# --- Compress tool (model-controlled compaction) ---

COMPRESS_TOOL = {
    "name": "compress",
    "description": (
        "Compress stale conversation context to free up token space. "
        "Use when the conversation is getting long and earlier messages "
        "are no longer needed verbatim. Preserves file changes, key "
        "decisions, and error resolutions. The LLM should call this "
        "proactively when it notices context getting large."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why compressing now (e.g. 'context getting long, earlier file reads no longer needed')",
            },
        },
        "required": ["reason"],
    },
}


def make_compress_handler(
    llm: Any,
    get_messages: Any,
    set_messages: Any,
) -> Any:
    """Create a compress tool handler with access to conversation state.

    Args:
        llm: LLMClient for summarization
        get_messages: Callable returning current message history
        set_messages: Callable accepting new compacted history
    """

    async def handler(input_data: dict[str, Any]) -> str:
        reason = input_data.get("reason", "context management")
        messages = get_messages()

        if len(messages) <= 8:
            return "Nothing to compress -- conversation is short."

        try:
            compacted = await compact_history(llm, messages, keep_recent=6)
            old_count = len(messages)
            new_count = len(compacted)
            set_messages(compacted)

            # Estimate token savings
            old_chars = sum(len(str(m.get("content", ""))) for m in messages)
            new_chars = sum(len(str(m.get("content", ""))) for m in compacted)
            saved = old_chars - new_chars

            return (
                f"Compressed conversation: {old_count} -> {new_count} messages "
                f"(~{saved // 4} tokens freed). Reason: {reason}"
            )
        except Exception as exc:
            return f"Compression failed: {exc}"

    return handler
