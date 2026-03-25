"""Dynamic agent registry.

Pluggable from day one -- agents register themselves, and the orchestrator
discovers them at runtime. The Skill Builder uses register() to add
dynamically-created agents.
"""

from __future__ import annotations

import logging

from opentf.agents.base import AgentRole, BaseAgent, GuardrailPhase
from opentf.models.task import Task

log = logging.getLogger(__name__)


class AgentRegistry:
    """Registry for dynamic agent registration and lookup."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent. Overwrites if name already exists."""
        log.info("Registered agent: %s [%s] (%s)", agent.name, agent.role.value, agent.description)
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent | None:
        """Get an agent by name."""
        return self._agents.get(name)

    def unregister(self, name: str) -> bool:
        """Remove an agent by name. Returns True if found and removed."""
        if name in self._agents:
            del self._agents[name]
            log.info("Unregistered agent: %s", name)
            return True
        return False

    def find_for_task(self, task: Task) -> BaseAgent | None:
        """Find the first specialist that can handle a given task."""
        for agent in self._agents.values():
            if agent.role == AgentRole.SPECIALIST and agent.can_handle(task):
                return agent
        return None

    def get_guardrails(self, phase: GuardrailPhase | None = None) -> list[BaseAgent]:
        """Return all guardrail agents, optionally filtered by phase."""
        guardrails = [
            a for a in self._agents.values()
            if a.role == AgentRole.GUARDRAIL
        ]
        if phase is not None:
            guardrails = [g for g in guardrails if phase in g.phases]
        return guardrails

    def all_agents(self) -> list[BaseAgent]:
        """Return all registered agents."""
        return list(self._agents.values())

    def agent_descriptions(self) -> list[dict[str, str]]:
        """Return name + description for each specialist (used by brain layer).

        Excludes guardrails -- they run automatically, not by routing.
        """
        return [
            {
                "name": a.name,
                "description": a.description,
                "capabilities": ", ".join(a.capabilities),
            }
            for a in self._agents.values()
            if a.role == AgentRole.SPECIALIST
        ]
