"""Subagent system for isolated parallel task execution.

Inspired by Claude Code's subagent architecture: each subtask gets its own
fresh context window. Only the final result returns to the parent. Intermediate
tool calls and results stay inside the subagent's context.

Key benefits:
- Prevents context overflow on complex multi-file tasks
- Enables parallel execution of independent subtasks
- Clean separation of concerns between parent and child
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.tools.file_tools import ALL_TOOLS, all_handlers, CODE_SAFE_COMMANDS

log = logging.getLogger(__name__)

MAX_CONCURRENT_SUBAGENTS = 5
DEFAULT_SUBAGENT_ITERATIONS = 20


@dataclass
class SubAgentResult:
    """Result from a subagent execution."""
    prompt: str
    response: str
    tokens_used: int
    success: bool
    error: str = ""


class SubAgent:
    """An isolated agent with its own context window and tool loop.

    When isolate=True, runs in a git worktree for full filesystem isolation.
    """

    def __init__(
        self,
        llm: LLMClient,
        prompt: str,
        system: str = "",
        tools: list[dict] | None = None,
        handlers: dict[str, Callable] | None = None,
        max_iterations: int = DEFAULT_SUBAGENT_ITERATIONS,
        bus: Any | None = None,
        name: str = "subagent",
        isolate: bool = False,
    ) -> None:
        self.llm = llm
        self.prompt = prompt
        self.system = system or self._default_system()
        self.tools = tools if tools is not None else list(ALL_TOOLS)
        self.handlers = handlers if handlers is not None else all_handlers(CODE_SAFE_COMMANDS)
        self.max_iterations = max_iterations
        self.bus = bus
        self.name = name
        self.isolate = isolate

    async def run(self) -> SubAgentResult:
        """Execute the subagent with a fresh, isolated context.

        Only the final text response is returned. All intermediate tool
        calls and results stay inside this context.

        If isolate=True, runs in a git worktree and merges changes back.
        """
        worktree_ctx = None
        original_base_dirs = None

        if self.isolate:
            try:
                from opentf.core.worktree import create_worktree, merge_worktree_changes, cleanup_worktree
                from opentf.tools.file_tools import ALLOWED_BASE_DIRS
                worktree_ctx = await create_worktree(self.name)
                if worktree_ctx:
                    # Patch sandbox to point at worktree
                    original_base_dirs = list(ALLOWED_BASE_DIRS)
                    ALLOWED_BASE_DIRS.clear()
                    ALLOWED_BASE_DIRS.append(worktree_ctx.worktree_path)
            except Exception as exc:
                log.warning("Worktree isolation failed for %s, running in-place: %s", self.name, exc)

        try:
            loop = ToolLoop(
                llm=self.llm,
                tools=self.tools,
                handlers=self.handlers,
                max_iterations=self.max_iterations,
                bus=self.bus,
                source=self.name,
            )

            # Fresh message history -- isolated from parent
            messages = [{"role": "user", "content": self.prompt}]

            text, tokens = await loop.run(
                messages=messages,
                system=self.system,
                temperature=0.3,
            )

            # Merge worktree changes back if isolated
            merge_info = ""
            if worktree_ctx:
                try:
                    from opentf.core.worktree import merge_worktree_changes
                    merge_info = await merge_worktree_changes(worktree_ctx)
                    if merge_info:
                        text += f"\n\n[Worktree merge: {merge_info}]"
                except Exception as exc:
                    text += f"\n\n[Worktree merge failed: {exc}]"

            return SubAgentResult(
                prompt=self.prompt,
                response=text,
                tokens_used=tokens,
                success=True,
            )
        except Exception as exc:
            log.error("Subagent %s failed: %s", self.name, exc)
            return SubAgentResult(
                prompt=self.prompt,
                response="",
                tokens_used=0,
                success=False,
                error=str(exc),
            )
        finally:
            # Restore original sandbox and clean up worktree
            if original_base_dirs is not None:
                from opentf.tools.file_tools import ALLOWED_BASE_DIRS
                ALLOWED_BASE_DIRS.clear()
                ALLOWED_BASE_DIRS.extend(original_base_dirs)
            if worktree_ctx:
                try:
                    from opentf.core.worktree import cleanup_worktree
                    await cleanup_worktree(worktree_ctx)
                except Exception:
                    pass

    @staticmethod
    def _default_system() -> str:
        return (
            "You are a focused subagent working on a specific subtask within a larger project.\n\n"
            "Rules:\n"
            "1. ALWAYS read a file before editing it. Never edit based on assumptions.\n"
            "2. Use edit_file (not write_file) for modifications to existing files.\n"
            "3. Match the existing code style (indentation, naming conventions, patterns).\n"
            "4. After making changes, verify them by reading the modified file back.\n"
            "5. Report exactly what you changed, including file paths and a brief summary.\n"
            "6. If you encounter an error, explain it clearly -- do not retry silently.\n"
            "7. Do not spawn additional subagents -- you are the executor.\n\n"
            "Be concise. Use tools directly. Do not narrate your intentions before acting."
        )


class SubAgentManager:
    """Manages spawning and coordinating subagents."""

    def __init__(
        self,
        llm: LLMClient,
        bus: Any | None = None,
        max_concurrent: int = MAX_CONCURRENT_SUBAGENTS,
    ) -> None:
        self.llm = llm
        self.bus = bus
        self.max_concurrent = max_concurrent

    async def spawn(
        self,
        prompt: str,
        system: str = "",
        name: str = "subagent",
        max_iterations: int = DEFAULT_SUBAGENT_ITERATIONS,
        tools: list[dict] | None = None,
        handlers: dict[str, Callable] | None = None,
    ) -> SubAgentResult:
        """Spawn a single subagent and wait for its result."""
        agent = SubAgent(
            llm=self.llm,
            prompt=prompt,
            system=system,
            tools=tools,
            handlers=handlers,
            max_iterations=max_iterations,
            bus=self.bus,
            name=name,
        )
        return await agent.run()

    async def spawn_parallel(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[SubAgentResult]:
        """Spawn multiple subagents in parallel with concurrency limits.

        Each task dict should have:
            prompt: str (required)
            name: str (optional)
            system: str (optional)
            max_iterations: int (optional)

        Returns results in the same order as input tasks.
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _run_with_limit(task: dict) -> SubAgentResult:
            async with semaphore:
                return await self.spawn(
                    prompt=task["prompt"],
                    name=task.get("name", "subagent"),
                    system=task.get("system", ""),
                    max_iterations=task.get("max_iterations", DEFAULT_SUBAGENT_ITERATIONS),
                )

        results = await asyncio.gather(
            *[_run_with_limit(t) for t in tasks],
            return_exceptions=False,
        )
        return list(results)


# --- Tool definition for MainAgent to spawn subagents ---

SPAWN_SUBAGENT_TOOL = {
    "name": "spawn_subagent",
    "description": (
        "Spawn an isolated subagent to handle a focused subtask. "
        "The subagent gets its own context window with file tools. "
        "Use for complex tasks that benefit from focused attention, "
        "or to parallelize independent research/editing tasks. "
        "Only the final result is returned."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed task description for the subagent",
            },
            "name": {
                "type": "string",
                "description": "Short name for the subagent (for logging)",
            },
        },
        "required": ["prompt"],
    },
}


def make_subagent_handler(
    llm: LLMClient,
    bus: Any | None = None,
) -> Callable:
    """Create a handler for the spawn_subagent tool."""
    manager = SubAgentManager(llm=llm, bus=bus)

    async def handler(input_data: dict[str, Any]) -> str:
        prompt = input_data["prompt"]
        name = input_data.get("name", "subagent")

        result = await manager.spawn(prompt=prompt, name=name)

        if result.success:
            return f"[Subagent '{name}' completed ({result.tokens_used} tokens)]\n\n{result.response}"
        else:
            return f"[Subagent '{name}' failed: {result.error}]"

    return handler
