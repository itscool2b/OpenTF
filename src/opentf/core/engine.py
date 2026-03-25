"""OpenTF engine: wires all components together.

Single-agent architecture: MainAgent handles code, files, research, data,
and conversation. Planner and SkillBuilder are separate callable agents.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from opentf.agents.guardrails.context import ContextAgent
from opentf.agents.guardrails.security import SecurityAgent
from opentf.agents.registry import AgentRegistry
from opentf.agents.specialists.janitor import JanitorAgent
from opentf.agents.specialists.main_agent import MainAgent
from opentf.agents.specialists.planner import PlannerAgent
from opentf.agents.specialists.skill_builder import SkillBuilderAgent
from opentf.core.bus import MessageBus
from opentf.core.orchestrator import Orchestrator
from opentf.llm.client import LLMClient
from opentf.models.message import Message

log = logging.getLogger(__name__)

MessageHandler = Callable[[Message], Awaitable[None]]


class Engine:
    """High-level API for the OpenTF orchestration pipeline."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        on_message: MessageHandler | None = None,
        provider_name: str = "anthropic",
    ) -> None:
        self.llm = LLMClient(provider_name=provider_name, api_key=api_key)
        if model:
            self.llm.model = model
        if temperature is not None:
            self.llm.temperature = temperature

        self.bus = MessageBus()
        self.registry = AgentRegistry()
        self.orchestrator = Orchestrator(
            llm=self.llm,
            registry=self.registry,
            bus=self.bus,
        )
        self.conversation_history: list[dict[str, Any]] = []

        self._register_agents()
        if on_message:
            self.bus.subscribe_all(on_message)

    def _register_agents(self) -> None:
        """Register agents for single-agent architecture."""
        from opentf.core.workspace import scan_workspace
        workspace = scan_workspace()

        # Pre-gates (rule-based, 0 tokens)
        self.registry.register(SecurityAgent())
        self.registry.register(ContextAgent(
            retriever=self._create_retriever(),
            bus=self.bus,
            workspace=workspace,
        ))

        # Primary agent (handles code, files, research, data, conversation)
        self.registry.register(MainAgent(self.llm))

        # Specialist agents
        self.registry.register(JanitorAgent(self.llm))

        # Separate agents (need their own reasoning)
        self.registry.register(PlannerAgent(self.llm, self.registry))
        skill_builder = SkillBuilderAgent(self.llm, self.registry)
        self.registry.register(skill_builder)
        skill_builder.load_saved_skills()

    def _create_retriever(self) -> Any:
        try:
            from opentf.memory.retriever import HybridRetriever
            from opentf.memory.store import MemoryStore
            return HybridRetriever(store=MemoryStore())
        except Exception as exc:
            log.warning("Memory subsystem unavailable: %s", exc)
            return None

    async def run(self, user_input: str) -> list[dict[str, Any]]:
        self.conversation_history.append({"role": "user", "content": user_input})

        results = await self.orchestrator.run(
            user_input,
            conversation_history=self.conversation_history,
        )

        outputs = []
        for result in results:
            outputs.append({
                "success": result.success,
                "output": result.output,
                "errors": result.errors,
                "token_usage": result.token_usage,
            })
            if result.success:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": str(result.output),
                })

        return outputs

    async def close(self) -> None:
        """Release all resources."""
        try:
            context_agent = self.registry.get("context")
            if context_agent and hasattr(context_agent, "_retriever") and context_agent._retriever:
                await context_agent._retriever.close()
        except Exception:
            pass
        self.bus.clear()
        self.conversation_history.clear()
        log.info("Engine shut down.")

    async def __aenter__(self) -> "Engine":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
