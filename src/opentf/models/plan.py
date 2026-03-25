"""Structured execution plan models.

Plans have phases, phases have steps. Steps specify which specialist
handles them, what they depend on, and what success looks like.
Serializable to/from YAML for persistence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """A single executable step within a phase."""

    id: str                          # e.g. "1.1", "2.3"
    name: str                        # human-readable step name
    description: str                 # what this step does
    agent_type: str                  # which specialist handles it
    depends_on: list[str] = []       # step IDs this depends on
    success_criteria: str = ""       # what "done" looks like
    status: StepStatus = StepStatus.PENDING
    output: dict[str, Any] = {}      # filled during execution
    error: str = ""


class PlanPhase(BaseModel):
    """A named group of steps that execute together."""

    name: str
    description: str = ""
    steps: list[PlanStep] = []


class Plan(BaseModel):
    """A complete execution plan with phases and steps."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    label: str                       # auto-generated kebab-case label
    description: str                 # one-line summary
    goal: str                        # original user request
    phases: list[PlanPhase] = []
    status: str = "draft"            # draft/confirmed/executing/completed/failed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict for YAML serialization."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "phases": [
                {
                    "name": phase.name,
                    "description": phase.description,
                    "steps": [
                        {
                            "id": step.id,
                            "name": step.name,
                            "description": step.description,
                            "agent_type": step.agent_type,
                            "depends_on": step.depends_on,
                            "success_criteria": step.success_criteria,
                            "status": step.status.value,
                            "error": step.error,
                        }
                        for step in phase.steps
                    ],
                }
                for phase in self.phases
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        """Reconstruct from a plain dict (loaded from YAML)."""
        phases = []
        for phase_data in data.get("phases", []):
            steps = []
            for step_data in phase_data.get("steps", []):
                steps.append(PlanStep(
                    id=step_data["id"],
                    name=step_data["name"],
                    description=step_data["description"],
                    agent_type=step_data["agent_type"],
                    depends_on=step_data.get("depends_on", []),
                    success_criteria=step_data.get("success_criteria", ""),
                    status=StepStatus(step_data.get("status", "pending")),
                    error=step_data.get("error", ""),
                ))
            phases.append(PlanPhase(
                name=phase_data["name"],
                description=phase_data.get("description", ""),
                steps=steps,
            ))
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            label=data["label"],
            description=data.get("description", ""),
            goal=data.get("goal", ""),
            phases=phases,
            status=data.get("status", "draft"),
        )

    def format_markdown(self) -> str:
        """Render plan as readable markdown."""
        lines = [f"## Plan: {self.label}\n"]
        if self.description:
            lines.append(f"_{self.description}_\n")

        for phase in self.phases:
            lines.append(f"\n### {phase.name}")
            if phase.description:
                lines.append(f"_{phase.description}_\n")
            for step in phase.steps:
                icon = _status_icon(step.status)
                deps = ""
                if step.depends_on:
                    deps = f" -- depends on {', '.join(step.depends_on)}"
                status_text = ""
                if step.status == StepStatus.RUNNING:
                    status_text = " -- running..."
                elif step.status == StepStatus.FAILED:
                    status_text = f" -- failed: {step.error[:60]}"
                lines.append(
                    f"  {icon} **{step.id}** {step.name} "
                    f"(`{step.agent_type}`){deps}{status_text}"
                )
                if step.success_criteria:
                    lines.append(f"    _Success: {step.success_criteria}_")

        return "\n".join(lines)

    def all_steps(self) -> list[PlanStep]:
        """Flatten all steps across phases."""
        return [step for phase in self.phases for step in phase.steps]


def _status_icon(status: StepStatus) -> str:
    if status == StepStatus.PENDING:
        return "[ ]"
    elif status == StepStatus.RUNNING:
        return "[>]"
    elif status == StepStatus.DONE:
        return "[x]"
    elif status == StepStatus.FAILED:
        return "[!]"
    elif status == StepStatus.SKIPPED:
        return "[-]"
    return "[ ]"
