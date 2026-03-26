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
    handle_read_file,
    handle_list_directory,
    handle_search_files,
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
- read_file, write_file, edit_file, apply_diff: File operations (sandboxed to project)
- batch_edit: Apply multiple edits atomically across files
- find_references, replace_in_files: Codebase-wide search and replace
- list_directory, search_files: Project exploration
- run_command: Shell commands (safe auto-approve, others need approval)
- spawn_subagent: Delegate focused subtasks to isolated agents
- web_search, read_url: Web research (if available)

You also have:
- Persistent memory across sessions
- Workspace awareness: language, framework, test command, file structure
- Repository map: key function/class signatures ranked by importance
- File safety: every change is backed up, user can /undo
- Auto-test: test failures after edits are fed back to you automatically

Approach:
1. IMPORTANT: Always read_file before edit_file. Never edit based on memory or assumptions.
2. For complex tasks: Think step by step. Read relevant files first.
3. For multi-file changes: Use batch_edit or spawn_subagent for parallel work.
4. Prefer edit_file over write_file for modifications.
5. Match existing code style.
6. Report briefly what you did after completing the task.
7. Do not narrate routine tool calls -- just call the tool."""

ARCHITECT_PROMPT = """\
You are an expert software architect. Analyze the codebase and plan the changes \
needed to accomplish the user's task.

DO NOT make any file changes. Instead:
1. Read the relevant files to understand the current state
2. Think through the architecture and approach
3. Output a detailed plan listing:
   - Which files need to change and why
   - What specific changes to make in each file
   - Any new files to create
   - Any tests to add or update
   - Potential risks or edge cases

Be specific about the changes -- include function names, line references, \
and exact code patterns to look for."""

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


# Signals for complex tasks that benefit from architect mode
_ARCHITECT_SIGNALS = {
    "refactor", "redesign", "rewrite", "restructure", "architect",
    "overhaul", "migrate", "implement feature", "add feature",
}


def _needs_architect(text: str) -> bool:
    """Check if task is complex enough to benefit from architect mode."""
    lower = text.lower()
    # Explicit triggers
    if any(sig in lower for sig in _ARCHITECT_SIGNALS):
        return True
    # Long detailed requests likely benefit from planning
    if len(text) > 200 and _needs_tools(text):
        return True
    return False


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

        # Architect mode: for complex tasks, plan first then execute
        if _needs_architect(user_input):
            architect_plan = await self._architect_pass(messages, context)
            if architect_plan:
                # Inject the plan as context for the editor pass
                messages.append({"role": "assistant", "content": architect_plan})
                messages.append({
                    "role": "user",
                    "content": "Now execute the plan above. Use tools to make the changes.",
                })

        # Tool-enabled: unified tool set
        tools, handlers = self._build_tools()
        system = self._build_system_prompt(context)
        bus = context.constraints.get("_bus")

        # Auto-test hook: run detected test command after edits
        test_cmd = context.constraints.get("test_command", "")
        post_edit_hook = None
        if test_cmd:
            from opentf.tools.file_tools import _execute_command

            async def _auto_test() -> str | None:
                result = await _execute_command(test_cmd)
                # Only feed back failures
                lower = result.lower()
                if any(kw in lower for kw in ("failed", "error", "traceback", "fail")):
                    return result[:3000]  # Cap test output
                return None

            post_edit_hook = _auto_test

        # Wire actual compress handler with message access
        try:
            from opentf.core.compaction import make_compress_handler
            handlers["compress"] = make_compress_handler(
                llm=self.llm,
                get_messages=lambda: messages,
                set_messages=lambda new_msgs: (messages.clear(), messages.extend(new_msgs)),
            )
        except ImportError:
            pass

        loop = ToolLoop(
            llm=self.llm,
            tools=tools,
            handlers=handlers,
            max_iterations=30,
            bus=bus,
            source="main",
            on_stream=on_stream,
            post_edit_hook=post_edit_hook,
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

    async def _architect_pass(
        self, messages: list[dict], context: AgentContext,
    ) -> str | None:
        """Architect pass: reason about changes without making them.

        Inspired by Aider's architect mode -- separates planning from editing.
        Uses read-only tools (read_file, list_directory, search_files) to
        understand the codebase, then outputs a detailed plan.
        """
        # Read-only tools for the architect
        read_tools = [
            t for t in ALL_TOOLS
            if t["name"] in ("read_file", "list_directory", "search_files")
        ]
        read_handlers = {
            "read_file": handle_read_file,
            "list_directory": handle_list_directory,
            "search_files": handle_search_files,
        }

        system = ARCHITECT_PROMPT
        repo_map = context.constraints.get("repo_map", "")
        if repo_map:
            system += f"\n\n{repo_map}"

        loop = ToolLoop(
            llm=self.llm,
            tools=read_tools,
            handlers=read_handlers,
            max_iterations=10,
            source="architect",
        )

        try:
            plan_text, _ = await loop.run(
                messages=list(messages),
                system=system,
                temperature=0.3,
            )
            if plan_text and len(plan_text) > 50:
                return plan_text
        except Exception as exc:
            log.warning("Architect pass failed (non-fatal): %s", exc)

        return None

    def _build_tools(self) -> tuple[list[dict], dict]:
        """Build unified tool set -- all tools available."""
        tools = list(FILE_TOOLS)
        handlers = file_handlers(CODE_SAFE_COMMANDS)

        # Unified diff tool
        try:
            from opentf.tools.diff_tools import diff_tools, diff_handlers
            tools.extend(diff_tools())
            handlers.update(diff_handlers())
        except ImportError:
            pass

        if _HAS_WEB:
            tools.extend(RESEARCH_TOOLS)
            handlers["web_search"] = _web_search
            handlers["read_url"] = _read_url

        tools.extend(DATA_TOOLS)
        handlers.update(data_handlers())

        tools.extend(GIT_TOOLS)
        handlers.update(git_handlers())

        # Refactoring tools (batch edit, find references, replace in files)
        try:
            from opentf.tools.refactor_tools import refactor_tools, refactor_handlers
            tools.extend(refactor_tools())
            handlers.update(refactor_handlers())
        except ImportError:
            pass

        # Subagent tool (isolated context, parallel execution)
        try:
            from opentf.core.subagent import SPAWN_SUBAGENT_TOOL, make_subagent_handler
            tools.append(SPAWN_SUBAGENT_TOOL)
            handlers["spawn_subagent"] = make_subagent_handler(self.llm)
        except ImportError:
            pass

        # Compress tool definition (handler wired in process() with message access)
        try:
            from opentf.core.compaction import COMPRESS_TOOL
            tools.append(COMPRESS_TOOL)
        except ImportError:
            pass

        # LSP diagnostics tool (shared manager for post-edit feedback)
        try:
            from opentf.tools.lsp_client import DIAGNOSTICS_TOOL, LSPManager, make_diagnostics_handler
            from opentf.tools.file_tools import set_lsp_manager
            manager = LSPManager()
            set_lsp_manager(manager)  # Share with file_tools for post-edit diagnostics
            tools.append(DIAGNOSTICS_TOOL)
            handlers["diagnostics"] = make_diagnostics_handler(manager)
        except ImportError:
            pass

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

        # Repo map (tree-sitter + PageRank ranked symbols)
        repo_map = context.constraints.get("repo_map", "")
        if repo_map:
            base += f"\n\n{repo_map}"

        memory = context.constraints.get("memory_context", "")
        if memory:
            base += f"\n\nRelevant memories from past sessions:\n{memory}"

        return base
