"""Blueprint models for the /task-force command.

A Blueprint describes a project decomposed into components,
assigned to dynamically-created specialist agents,
ordered into parallel execution waves.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from opentf.cli.theme import COLORS


class Specialist(BaseModel):
    """A dynamically created specialist agent definition."""

    name: str
    role: str
    scope: str
    system_prompt: str
    depends_on: list[str] = []


class Component(BaseModel):
    """A piece of the project to build."""

    name: str
    description: str
    files: list[str] = []
    specialist: str
    depends_on: list[str] = []
    status: str = "pending"  # pending/running/done/failed


class Blueprint(BaseModel):
    """The Architect's plan for the entire project."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    goal: str
    components: list[Component] = []
    specialists: list[Specialist] = []
    waves: list[list[str]] = []  # [[specialist names in wave], ...]
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def format_display(self) -> str:
        """Rich markup display for checkpoints."""
        A = COLORS['accent']
        B = COLORS['border']
        D = COLORS['text_dim']
        S = COLORS['success']
        E = COLORS['error']

        lines = [
            f"[bold {A}]{'─' * 40}[/]",
            f"  [bold]Blueprint[/]",
            f"  [{D}]{self.goal}[/]",
            f"  [{D}]{len(self.components)} components, "
            f"{len(self.specialists)} specialists, "
            f"{len(self.waves)} waves[/]",
            f"[{B}]{'─' * 40}[/]",
        ]

        # Specialists
        lines.append("")
        lines.append("  [bold]Specialists[/]")
        for spec in self.specialists:
            lines.append(f"    [{A}]{spec.name:<16}[/] [{D}]{spec.role}[/]")

        # Components
        lines.append("")
        lines.append("  [bold]Components[/]")
        for comp in self.components:
            status_color = {"done": S, "failed": E, "running": A}.get(comp.status, D)
            deps = f" [{D}]depends on {', '.join(comp.depends_on)}[/]" if comp.depends_on else ""
            lines.append(
                f"    [{status_color}]{comp.name:<16}[/] [{D}]{comp.description[:50]}[/]{deps}"
            )

        # Waves
        lines.append("")
        lines.append("  [bold]Execution Waves[/]")
        for i, wave in enumerate(self.waves):
            agents = ", ".join(wave)
            parallel = " (parallel)" if len(wave) > 1 else ""
            lines.append(f"    Wave {i + 1}: [{A}]{agents}[/]{parallel}")

        lines.append("")
        lines.append(f"[{B}]{'─' * 40}[/]")
        lines.append(
            f"  [{A}]/confirm[/] proceed   "
            f"[{COLORS['error']}]/cancel[/] abort   "
            f"[{D}]or type feedback to refine[/]"
        )

        return "\n".join(lines)

    def format_summary(self) -> str:
        """Rich markup completion summary."""
        A = COLORS['accent']
        B = COLORS['border']
        D = COLORS['text_dim']
        S = COLORS['success']
        E = COLORS['error']

        done = sum(1 for c in self.components if c.status == "done")
        failed = sum(1 for c in self.components if c.status == "failed")
        total = len(self.components)

        color = S if self.status == "completed" else E
        label = "TASK FORCE COMPLETE" if self.status == "completed" else "TASK FORCE FAILED"

        lines = [
            "",
            f"[bold {color}]{'─' * 40}[/]",
            f"  [bold]{label}[/]",
            f"  [{D}]{self.goal}[/]",
            f"  [{D}]{done}/{total} components done[/]",
            f"[bold {color}]{'─' * 40}[/]",
            "",
        ]

        for comp in self.components:
            status_color = {"done": S, "failed": E}.get(comp.status, D)
            icon = {"done": "[x]", "failed": "[!]", "pending": "[ ]"}.get(comp.status, "[ ]")
            lines.append(f"    {icon} [{status_color}]{comp.name}[/] [{D}]{comp.description[:40]}[/]")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_plan(plan_data: dict) -> str:
        """Rich markup display for the lightweight plan stage (before specialists)."""
        A = COLORS['accent']
        B = COLORS['border']
        D = COLORS['text_dim']

        components = plan_data.get("components", [])
        lines = [
            f"[bold {A}]{'─' * 40}[/]",
            f"  [bold]Task Force Plan[/]",
            f"  [{D}]{len(components)} components[/]",
            f"[{B}]{'─' * 40}[/]",
            "",
        ]

        for comp in components:
            deps = ""
            if comp.get("depends_on"):
                deps = f" [{D}]depends on {', '.join(comp['depends_on'])}[/]"
            lines.append(f"    [{A}]{comp['name']:<16}[/] {comp.get('description', '')[:50]}{deps}")

        lines.append("")
        lines.append(f"[{B}]{'─' * 40}[/]")
        lines.append(
            f"  [{A}]/confirm[/] proceed to architecture   "
            f"[{COLORS['error']}]/cancel[/] abort   "
            f"[{D}]or type feedback[/]"
        )

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "components": [c.model_dump() for c in self.components],
            "specialists": [s.model_dump() for s in self.specialists],
            "waves": self.waves,
        }
