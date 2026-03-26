"""Structured execution plan models.

Plans have phases, phases have steps. Steps specify which specialist
handles them, what they depend on, and what success looks like.
Serializable to/from YAML for persistence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from opentf.cli.theme import COLORS, GLYPHS, gradient_text
from opentf.cli.renderables import (
    animated_progress_bar, plan_step_icon, section_header, section_footer,
    status_badge, tree_connector,
)
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
        """Render plan as readable Rich markup with styled icons and tree connectors."""
        D = COLORS['text_dim']
        A = COLORS['accent']
        lines = [
            section_header(f"Plan: {self.label}", 50),
        ]
        if self.description:
            lines.append(f"  [{D}]{self.description}[/]")
        lines.append("")

        for phase in self.phases:
            lines.append(f"  {gradient_text(phase.name)}")
            if phase.description:
                lines.append(f"  [{D}]{phase.description}[/]")
            for i, step in enumerate(phase.steps):
                icon = plan_step_icon(step.status.value)

                # Dependencies as tree connectors
                deps = ""
                if step.depends_on:
                    dep_list = ", ".join(step.depends_on)
                    is_last = i == len(phase.steps) - 1
                    connector = tree_connector(is_last)
                    deps = f" {connector} [{D}]{dep_list}[/]"

                # Agent type badge
                agent_badge = status_badge(step.agent_type, COLORS['text_muted'], bold=False)

                # Status annotation
                status_text = ""
                if step.status == StepStatus.RUNNING:
                    status_text = f" [{A}]running...[/]"
                elif step.status == StepStatus.FAILED:
                    status_text = f" [{COLORS['error']}]failed: {step.error[:50]}[/]"

                lines.append(
                    f"    {icon} [bold]{step.id}[/] {step.name} "
                    f"{agent_badge}{deps}{status_text}"
                )
                if step.success_criteria:
                    lines.append(f"         [{D}]{step.success_criteria}[/]")

        lines.append(section_footer(50))
        return "\n".join(lines)

    def all_steps(self) -> list[PlanStep]:
        """Flatten all steps across phases."""
        return [step for phase in self.phases for step in phase.steps]

    def format_completion_summary(self) -> str:
        """Rich completion summary with progress bar and step recap."""
        steps = self.all_steps()
        done = sum(1 for s in steps if s.status == StepStatus.DONE)
        failed = sum(1 for s in steps if s.status == StepStatus.FAILED)
        skipped = sum(1 for s in steps if s.status == StepStatus.SKIPPED)
        total = len(steps)

        S = COLORS['success']
        E = COLORS['error']
        D = COLORS['text_dim']
        lines: list[str] = [""]

        if self.status == "completed":
            color = S
            title = "Plan Complete"
        else:
            color = E
            title = "Plan Failed"

        lines.append(section_header(title, 50, color=color))

        # Progress bar
        bar = animated_progress_bar(done, total, 30, frame=0)
        lines.append(f"  {bar}")

        # Stats line
        stats_parts: list[str] = [f"[{S}]{done} done[/]"]
        if failed:
            stats_parts.append(f"[{E}]{failed} failed[/]")
        if skipped:
            stats_parts.append(f"[{D}]{skipped} skipped[/]")
        stats_parts.append(f"[{D}]{total} total[/]")
        lines.append(f"  {' {0} '.format(GLYPHS['sep']).join(stats_parts)}")
        lines.append("")

        # Step recap
        for phase in self.phases:
            lines.append(f"  [bold]{phase.name}[/]")
            for step in phase.steps:
                icon = plan_step_icon(step.status.value)
                error = f" [{E}]{step.error[:50]}[/]" if step.error else ""
                lines.append(f"    {icon} {step.id} {step.name}{error}")
            lines.append("")

        lines.append(section_footer(50, color=color))
        return "\n".join(lines)


def _status_icon(status: StepStatus) -> str:
    """Legacy icon function -- delegates to plan_step_icon."""
    return plan_step_icon(status.value)
