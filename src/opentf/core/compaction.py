"""Smart conversation compaction.

When conversation history approaches the context limit, summarize older
messages with an LLM call instead of dropping them. Preserves key context
like decisions, file changes, and user preferences.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

COMPACTION_PROMPT = """\
Summarize this conversation concisely. Preserve:
- Key decisions made
- Files created, modified, or deleted
- User preferences and corrections
- Technical context (languages, frameworks, project structure)
- Error resolutions and what was tried
- Current state of work in progress

Output a brief summary paragraph. No preamble."""


async def compact_history(
    llm: Any,
    history: list[dict[str, Any]],
    keep_recent: int = 4,
) -> list[dict[str, Any]]:
    """Summarize older conversation history, keeping recent messages intact.

    Args:
        llm: LLMClient instance.
        history: Full conversation history.
        keep_recent: Number of recent messages to keep verbatim.

    Returns:
        Compacted history: [summary_message] + recent messages.
    """
    if len(history) <= keep_recent + 1:
        return history

    to_compact = history[:-keep_recent]
    to_keep = history[-keep_recent:]

    # Build the conversation to summarize
    summary_msgs = [
        {"role": "user", "content": _format_for_summary(to_compact)},
    ]

    try:
        summary_text = await llm.complete_text(
            messages=summary_msgs,
            system=COMPACTION_PROMPT,
            temperature=0.3,
        )
    except Exception as exc:
        log.warning("Compaction failed: %s. Falling back to truncation.", exc)
        return history[-keep_recent:]

    summary_message = {
        "role": "user",
        "content": f"[Prior conversation summary]\n{summary_text}",
    }

    log.info(
        "Compacted %d messages into summary (%d chars)",
        len(to_compact), len(summary_text),
    )

    return [summary_message] + to_keep


def _format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Format messages into a readable block for the summarizer."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))
        # Truncate very long messages to keep summary input reasonable
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)
