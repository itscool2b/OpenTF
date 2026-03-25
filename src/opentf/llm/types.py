"""Provider-agnostic LLM response types.

These dataclasses mirror the attribute access patterns used throughout
the codebase (tool_loop.py, main_agent.py, etc.) so that switching
providers requires zero changes to consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    """A text content block from the LLM response."""

    type: str = "text"
    text: str = ""


@dataclass
class ToolUseBlock:
    """A tool_use content block from the LLM response."""

    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


ContentBlock = TextBlock | ToolUseBlock


@dataclass
class Usage:
    """Token usage from a single LLM request."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0  # Anthropic-only, 0 for others
    cache_read_input_tokens: int = 0      # Anthropic-only, 0 for others


@dataclass
class LLMResponse:
    """Provider-agnostic LLM response.

    Preserves the exact attribute interface used by tool_loop.py:
      - response.content[0].text
      - block.type == "tool_use"
      - block.name, block.input, block.id
      - response.usage.input_tokens, response.usage.output_tokens
    """

    content: list[ContentBlock] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    model: str = ""
