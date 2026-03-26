"""Context guardrail agent -- full context manager for the pipeline.

Not just memory retrieval. Maintains conversation state, tracks what each
agent has produced, retrieves relevant memories, and builds per-agent
execution context. Non-blocking -- always returns success=True.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from opentf.agents.base import AgentResult, AgentRole, BaseAgent, GuardrailPhase
from opentf.core.bus import MessageBus
from opentf.models.context import AgentContext
from opentf.models.message import Message, MessageType

log = logging.getLogger(__name__)

# Import retriever type conditionally to avoid hard dep
try:
    from opentf.memory.retriever import HybridRetriever
except ImportError:
    HybridRetriever = None  # type: ignore[misc,assignment]


@dataclass
class SessionState:
    """Tracks state across pipeline runs within a single session."""

    agent_outputs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    turn_count: int = 0
    topics: list[str] = field(default_factory=list)
    active_files: list[str] = field(default_factory=list)


class ContextAgent(BaseAgent):
    """Full context manager. Enriches tasks with workspace info, session state, agent history, and memories."""

    def __init__(
        self,
        retriever: Any | None = None,
        bus: MessageBus | None = None,
        workspace: Any | None = None,
    ) -> None:
        super().__init__(
            name="context",
            description="Builds execution context: conversation state, agent history, relevant memories",
            capabilities=["context"],
            role=AgentRole.GUARDRAIL,
            phases=[GuardrailPhase.PRE],
        )
        self._retriever = retriever
        self._bus = bus
        self._workspace = workspace
        self._session = SessionState()

        # Subscribe to bus for agent output tracking
        if bus:
            bus.subscribe(MessageType.TASK_COMPLETED, self._on_task_completed)

    async def _on_task_completed(self, message: Message) -> None:
        """Track agent outputs as they complete."""
        source = message.source
        if source not in self._session.agent_outputs:
            self._session.agent_outputs[source] = []
        self._session.agent_outputs[source].append({
            "task_id": message.task_id,
            "payload": message.payload,
            "timestamp": message.timestamp.isoformat(),
        })

        # Track files mentioned in code agent outputs
        if source == "code" and isinstance(message.payload, dict):
            files = message.payload.get("files", [])
            for f in files:
                if f and f not in self._session.active_files:
                    self._session.active_files.append(f)

    async def process(self, context: AgentContext) -> AgentResult:
        self._session.turn_count += 1
        enrichments: dict[str, Any] = {}

        # 1. Memory retrieval (past sessions)
        enrichments["memory_context"] = await self._retrieve_memories(
            context.task.description,
        )

        # 2. Session context (current session history)
        enrichments["session_context"] = self._build_session_context(
            context.conversation_history,
        )

        # 3. Agent execution history
        enrichments["agent_history"] = dict(self._session.agent_outputs)

        # 4. Active files
        enrichments["relevant_files"] = list(self._session.active_files)

        # 5. Workspace info (project structure, language, test command)
        if self._workspace:
            enrichments["workspace_summary"] = (
                f"Language: {self._workspace.language}\n"
                f"Framework: {self._workspace.framework}\n"
                f"Config files: {', '.join(self._workspace.config_files)}\n"
                f"Test command: {self._workspace.test_command}\n"
                f"Files: {self._workspace.file_count}\n"
                f"Project tree:\n{self._workspace.tree}"
            )
            enrichments["workspace_root"] = str(self._workspace.root)
            enrichments["test_command"] = self._workspace.test_command
            if self._workspace.repo_map_text:
                enrichments["repo_map"] = self._workspace.repo_map_text

        # 6. Context summary for the specialist
        enrichments["context_summary"] = self._build_summary(
            context.task.description,
            context.task.agent_type or "unknown",
            enrichments,
        )

        return AgentResult(success=True, output=enrichments)

    async def _retrieve_memories(self, query: str) -> str:
        """Search memory store for relevant past context."""
        if self._retriever is None:
            return ""

        try:
            memories = await self._retriever.search(query, top_k=3)
            if not memories:
                return ""

            # Filter by relevance
            relevant = [m for m in memories if m.distance < 0.8]
            if not relevant:
                return ""

            return "\n---\n".join(
                f"[{m.metadata.get('stored_at', 'unknown')}] {m.content}"
                for m in relevant
            )
        except Exception as exc:
            log.warning("Memory retrieval failed (non-blocking): %s", exc)
            return ""

    def _build_session_context(self, history: list[dict[str, Any]]) -> str:
        """Build a structured summary of the current conversation."""
        if not history:
            return ""

        # Summarize recent turns
        recent = history[-6:]  # last 3 exchanges
        parts = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))
            # Truncate long content
            if len(content) > 200:
                content = content[:200] + "..."
            parts.append(f"{role}: {content}")

        return "\n".join(parts)

    def _build_summary(
        self,
        task_description: str,
        agent_type: str,
        enrichments: dict[str, Any],
    ) -> str:
        """Build a brief context summary tailored to the specialist type."""
        parts = []

        if self._session.turn_count > 1:
            parts.append(f"Turn {self._session.turn_count} of this session.")

        # Include relevant agent history for the specialist
        if agent_type in ("code", "coding", "programming"):
            code_history = self._session.agent_outputs.get("code", [])
            if code_history:
                last = code_history[-1]
                parts.append(f"Previous code output available (task {last.get('task_id', 'unknown')[:8]}).")
            if self._session.active_files:
                parts.append(f"Active files: {', '.join(self._session.active_files[:5])}")

        if enrichments.get("memory_context"):
            parts.append("Relevant memories from past sessions found.")

        return " ".join(parts) if parts else ""
