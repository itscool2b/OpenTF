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
from opentf.tools.git_tools import GIT_TOOLS, git_handlers

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
You are OpenTF, an autonomous AI coding agent running in the user's terminal.

You have capabilities the user should know about:
- You can read, write, edit, and search files in the current project
- You can run shell commands (with user approval for non-safe commands)
- You can search the web and fetch URLs
- You have persistent memory across sessions -- you remember past tasks and solutions
- The user can save/resume conversation sessions (/save, /resume)
- You support planning mode (/plan) for structured multi-step tasks
- You support code scanning (/janitor) for quality issues
- File changes are backed up and undoable (/undo)

When the user asks about your capabilities, memory, or how you work, answer accurately \
based on the above. You DO have persistent memory via a vector database.

Respond naturally and concisely."""

AGENT_PROMPT = """\
You are OpenTF, an autonomous coding agent working in a real project directory.

IMPORTANT: Use the provided tools to accomplish tasks. When the user asks you to \
create, modify, or work with files, use the tools directly. Never output code for \
the user to copy-paste.

Tools available:
- read_file, write_file, edit_file: File operations (sandboxed to project directory)
- list_directory, search_files: Project exploration
- run_command: Shell commands (safe commands auto-approve, others need user approval)
- web_search, read_url: Web research (if available)

You also have:
- Persistent memory: Past interactions are stored and retrieved automatically. \
You can reference solutions from previous sessions.
- Workspace awareness: You know the project language, framework, test command, \
and file structure.
- File safety: Every file change is backed up. The user can /undo changes.

Guidelines:
- Do not narrate routine tool calls. Just call the tool.
- Prefer edit_file over write_file for modifications.
- Match existing code style.
- Report briefly what you did after completing the task."""

# Keywords that indicate tool access is needed
_TOOL_SIGNALS = {
    "create", "make", "write", "edit", "fix", "build", "run",
    "delete", "remove", "add", "change", "update", "modify",
    "read", "show", "list", "find", "search", "grep",
    "file", "folder", "directory", "code", "script", "test",
    "install", "commit", "push", "pull", "deploy", "refactor",
    "debug", "error", "bug", "implement", "generate", "save",
    "rename", "move", "copy", "open", "check", "scan", "analyze",
}


def _needs_tools(text: str) -> bool:
    """Check if input needs tool access. Action words always get tools."""
    words = set(text.lower().split())
    if words & _TOOL_SIGNALS:
        return True
    # Short messages without action words are pure conversation
    if len(text) < 30:
        return False
    # Long messages get tools by default
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

        # Save interaction to memory (non-blocking, best-effort)
        if text:
            try:
                from opentf.memory.store import MemoryStore
                store = MemoryStore()
                summary = f"Task: {user_input[:200]}\nResult: {text[:300]}"
                await store.store(summary, metadata={"type": "interaction"})
            except Exception:
                pass

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

        tools.extend(GIT_TOOLS)
        handlers.update(git_handlers())

        return tools, handlers

    def _build_system_prompt(self, context: AgentContext) -> str:
        """Build system prompt with workspace context and memory."""
        base = AGENT_PROMPT

        workspace = context.constraints.get("workspace_summary", "")
        if workspace:
            base += f"\n\nProject:\n{workspace}"
        test_cmd = context.constraints.get("test_command", "")
        if test_cmd:
            base += f"\nTest command: {test_cmd}"

        memory = context.constraints.get("memory_context", "")
        if memory:
            base += f"\n\nRelevant memories from past sessions:\n{memory}"

        return base
