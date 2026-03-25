"""MainAgent: single primary agent with unified tools.

One LLM conversation per request. All tools available for non-conversation
tasks. LLM decides which tools to use. Streaming for conversation mode.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from opentf.agents.base import AgentResult, AgentRole, BaseAgent
from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.models.context import AgentContext
from opentf.tools.file_tools import (
    ALL_TOOLS as FILE_TOOLS,
    CODE_SAFE_COMMANDS,
    all_handlers as file_handlers,
)
from opentf.tools.data_tools import DATA_TOOLS, data_handlers

log = logging.getLogger(__name__)

# Import research tools
try:
    from opentf.agents.specialists.research import (
        TOOLS as RESEARCH_TOOLS,
        _web_search,
        _read_url,
    )
    _HAS_WEB = True
except ImportError:
    RESEARCH_TOOLS = []
    _HAS_WEB = False

# --- Unified system prompt ---

CONVERSATION_PROMPT = """\
You are OpenTF, a helpful AI assistant. Respond naturally and concisely.
If the user asks a question, answer it. If they need help, help them."""

AGENT_PROMPT = """\
You are OpenTF, an autonomous coding agent working in a real project directory.

IMPORTANT: You MUST use the provided tools to accomplish tasks. When the user \
asks you to create, modify, or work with files, use write_file, edit_file, \
read_file, and other tools. Never output code for the user to manually copy-paste.

Available tools: read_file, write_file, edit_file, list_directory, search_files, run_command.

Workflow:
1. For new files: use write_file to create them directly.
2. For modifications: use read_file first, then edit_file to make changes.
3. For exploration: use list_directory and search_files.
4. For running code: use run_command.

Guidelines:
- Do not narrate routine tool calls. Just call the tool.
- Prefer edit_file over write_file for modifications (preserves unchanged code).
- Write minimal code. A hello world is one file.
- Don't create config files unless asked.
- Match existing code style.
- Report briefly what you did after completing the task."""

# Conversation-only signals -- everything else gets tools
_CONVERSATION_SIGNALS = {
    "hello", "hi ", "hey", "thanks", "thank you", "bye", "goodbye",
    "how are you", "what's up", "yo", "sup",
    "what is your", "who are you", "can you explain",
    "tell me about", "what do you think",
}


def _needs_tools(text: str) -> bool:
    """Most requests need tools. Only skip for pure chat/greetings."""
    text_lower = text.lower().strip()
    if len(text_lower) < 20 and any(text_lower.startswith(s) for s in _CONVERSATION_SIGNALS):
        return False
    return True


class MainAgent(BaseAgent):
    """Single primary agent. All tools for non-conversation, streaming for chat."""

    def __init__(self, llm: LLMClient) -> None:
        super().__init__(
            name="main",
            description="Primary agent -- handles everything",
            capabilities=[
                "code", "coding", "programming", "debug", "refactor",
                "file", "filesystem", "read_file", "write_file", "search_files",
                "system", "shell", "git",
                "research", "analyze", "summarize", "explain_topic", "compare",
                "fact_check", "web_search",
                "data", "csv", "json", "analyze_data", "statistics", "data_analysis",
                "conversation", "chat", "general", "question", "explain",
            ],
            role=AgentRole.SPECIALIST,
        )
        self.llm = llm

    async def process(self, context: AgentContext) -> AgentResult:
        on_stream = context.constraints.get("_on_stream")
        user_input = context.task.description

        messages: list[dict] = []
        for msg in context.conversation_history[-6:]:
            messages.append(msg)
        messages.append({"role": "user", "content": user_input})

        if not _needs_tools(user_input):
            # Conversation: no tools, stream if callback available
            system = CONVERSATION_PROMPT
            if on_stream:
                response = await self.llm.stream(
                    messages=messages,
                    system=system,
                    temperature=0.7,
                    on_text=on_stream,
                )
            else:
                response = await self.llm.complete(
                    messages=messages,
                    system=system,
                    temperature=0.7,
                )
            text = response.content[0].text if response.content else ""  # type: ignore[union-attr]
            tokens = response.usage.input_tokens + response.usage.output_tokens
            return AgentResult(
                success=True,
                output={"response": text, "_streamed": bool(on_stream)},
                token_usage=tokens,
            )

        # Tool-enabled: unified tool set
        tools, handlers = self._build_tools()
        system = self._build_system_prompt(context)
        bus = context.constraints.get("_bus")

        loop = ToolLoop(
            llm=self.llm,
            tools=tools,
            handlers=handlers,
            max_iterations=15,
            bus=bus,
            source="main",
            on_stream=on_stream,
        )

        text, tokens = await loop.run(
            messages=messages,
            system=system,
            temperature=0.3,
        )

        return AgentResult(
            success=True,
            output={"response": text, "_streamed": bool(on_stream)},
            token_usage=tokens,
        )

    def _build_tools(self) -> tuple[list[dict], dict]:
        """Build unified tool set -- all tools available."""
        tools = list(FILE_TOOLS)
        handlers = file_handlers(CODE_SAFE_COMMANDS)

        if _HAS_WEB:
            tools.extend(RESEARCH_TOOLS)
            handlers["web_search"] = _web_search
            handlers["read_url"] = _read_url

        tools.extend(DATA_TOOLS)
        handlers.update(data_handlers())

        return tools, handlers

    def _build_system_prompt(self, context: AgentContext) -> str:
        """Build system prompt with workspace context."""
        base = AGENT_PROMPT

        workspace = context.constraints.get("workspace_summary", "")
        if workspace:
            base += f"\n\nProject:\n{workspace}"
        test_cmd = context.constraints.get("test_command", "")
        if test_cmd:
            base += f"\nTest command: `{test_cmd}`"

        return base
