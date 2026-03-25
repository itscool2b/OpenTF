"""Scoped agent context model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from opentf.models.task import Task


class AgentContext(BaseModel):
    """Isolated context for a single agent execution.

    Each agent receives only the context it needs -- task state, relevant
    history, and constraints. No centralized shared memory. This prevents
    context contamination across agents.
    """

    task: Task
    conversation_history: list[dict[str, Any]] = []
    system_prompt: str = ""
    available_tools: list[str] = []
    constraints: dict[str, Any] = {}
