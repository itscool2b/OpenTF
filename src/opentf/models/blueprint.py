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

from opentf.cli.theme import COLORS, GLYPHS, gradient_text
from opentf.cli.renderables import (
    animated_progress_bar, key_badge, plan_step_icon,
    section_header, section_footer, status_badge, tree_connector,
)


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
        """Rich markup display for checkpoints with styled sections."""
        A = COLORS['accent']
        D = COLORS['text_dim']
        S = COLORS['success']
        E = COLORS['error']

        lines = [
            section_header("Blueprint", 50),
            f"  [{D}]{self.goal}[/]",
            f"  [{D}]{len(self.components)} components, "
            f"{len(self.specialists)} specialists, "
            f"{len(self.waves)} waves[/]",
            "",
        ]

        # Specialists as badge cards
        lines.append(f"  {gradient_text('Specialists')}")
        for spec in self.specialists:
            badge = status_badge(spec.name, A, bold=True)
            lines.append(f"    {badge} [{D}]{spec.role}[/]")

        # Components with tree connectors
        lines.append("")
        lines.append(f"  {gradient_text('Components')}")
        for i, comp in enumerate(self.components):
            status_color = {"done": S, "failed": E, "running": A}.get(comp.status, D)
            icon = plan_step_icon(comp.status)
            deps = ""
            if comp.depends_on:
                is_last = i == len(self.components) - 1
                connector = tree_connector(is_last)
                deps = f" {connector} [{D}]{', '.join(comp.depends_on)}[/]"
            lines.append(
                f"    {icon} [{status_color}]{comp.name:<16}[/] "
                f"[{D}]{comp.description[:45]}[/]{deps}"
            )

        # Waves as visual parallel diagram
        lines.append("")
        lines.append(f"  {gradient_text('Execution Waves')}")
        for i, wave in enumerate(self.waves):
            wave_num = f"[{COLORS['text_muted']}]Wave {i + 1}:[/]"
            agents_display = f"  ".join(
                f"[bold {A}]{GLYPHS['bullet']} {a}[/]" for a in wave
            )
            parallel = f" [{D}](parallel)[/]" if len(wave) > 1 else ""
            lines.append(f"    {wave_num}  {agents_display}{parallel}")

        lines.append("")
        lines.append(section_footer(50))
        lines.append(
            f"  {key_badge('/confirm', 'proceed')}   "
            f"{key_badge('/cancel', 'abort')}   "
            f"[{D}]or type feedback to refine[/]"
        )

        return "\n".join(lines)

    def format_summary(self) -> str:
        """Rich markup completion summary with progress bar."""
        D = COLORS['text_dim']
        S = COLORS['success']
        E = COLORS['error']

        done = sum(1 for c in self.components if c.status == "done")
        failed = sum(1 for c in self.components if c.status == "failed")
        total = len(self.components)

        color = S if self.status == "completed" else E
        title = "Task Force Complete" if self.status == "completed" else "Task Force Failed"

        lines = [
            "",
            section_header(title, 50, color=color),
            f"  [{D}]{self.goal}[/]",
            f"  {animated_progress_bar(done, total, 30, frame=0)}",
            f"  [{S}]{done} done[/] [{COLORS['text_muted']}]{GLYPHS['sep']}[/] "
            f"[{E}]{failed} failed[/] [{COLORS['text_muted']}]{GLYPHS['sep']}[/] "
            f"[{D}]{total} total[/]",
            "",
        ]

        for comp in self.components:
            icon = plan_step_icon(comp.status)
            status_color = {"done": S, "failed": E}.get(comp.status, D)
            lines.append(
                f"    {icon} [{status_color}]{comp.name}[/] "
                f"[{D}]{comp.description[:40]}[/]"
            )

        lines.append("")
        lines.append(section_footer(50, color=color))
        return "\n".join(lines)

    @staticmethod
    def format_plan(plan_data: dict) -> str:
        """Rich markup display for the lightweight plan stage (before specialists)."""
        A = COLORS['accent']
        D = COLORS['text_dim']

        components = plan_data.get("components", [])
        lines = [
            section_header("Task Force Plan", 50),
            f"  [{D}]{len(components)} components[/]",
            "",
        ]

        for i, comp in enumerate(components):
            deps = ""
            if comp.get("depends_on"):
                is_last = i == len(components) - 1
                connector = tree_connector(is_last)
                deps = f" {connector} [{D}]{', '.join(comp['depends_on'])}[/]"
            lines.append(
                f"    [{A}]{GLYPHS['bullet']}[/] "
                f"[bold {A}]{comp['name']:<16}[/] "
                f"{comp.get('description', '')[:45]}{deps}"
            )

        lines.append("")
        lines.append(section_footer(50))
        lines.append(
            f"  {key_badge('/confirm', 'proceed to architecture')}   "
            f"{key_badge('/cancel', 'abort')}   "
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
