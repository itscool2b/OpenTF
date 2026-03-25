"""Abstract agent interface, role enums, and result model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel

from opentf.models.context import AgentContext
from opentf.models.task import Task


class AgentRole(str, Enum):
    """Whether an agent is a guardrail (runs on every task) or a specialist (routed to)."""

    GUARDRAIL = "guardrail"
    SPECIALIST = "specialist"


class GuardrailPhase(str, Enum):
    """Pipeline phase a guardrail participates in."""

    PRE = "pre"
    POST = "post"


class AgentResult(BaseModel):
    """Structured result from an agent execution."""

    success: bool
    output: dict[str, Any] = {}
    errors: list[str] = []
    token_usage: int = 0


class BaseAgent(ABC):
    """Base class for all OpenTF agents.

    Every agent -- guardrail or specialist -- implements this interface.
    The registry discovers agents through `can_handle`, and the orchestrator
    calls `process` with a scoped context.

    Guardrails set role=GUARDRAIL and phases=[PRE] / [POST] / [PRE, POST].
    Specialists leave the defaults (role=SPECIALIST, phases=[]).
    """

    name: str
    description: str
    capabilities: list[str]
    role: AgentRole
    phases: list[GuardrailPhase]

    def __init__(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        role: AgentRole = AgentRole.SPECIALIST,
        phases: list[GuardrailPhase] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.capabilities = capabilities
        self.role = role
        self.phases = phases or []

    @abstractmethod
    async def process(self, context: AgentContext) -> AgentResult:
        """Process a task within its scoped context and return a result."""
        ...

    def can_handle(self, task: Task) -> bool:
        """Whether this agent can handle the given task type."""
        return task.agent_type in self.capabilities

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} role={self.role.value}>"
