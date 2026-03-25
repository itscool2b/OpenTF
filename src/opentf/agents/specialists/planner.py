"""Planner specialist agent -- flat tools, no nested agents.

Uses direct tools (web_search, read_file, etc.) in a single ToolLoop conversation.
No nested agent calls, no self-critique LLM calls. One conversation generates the plan.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opentf.agents.base import AgentResult, AgentRole, BaseAgent
from opentf.agents.registry import AgentRegistry
from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.models.context import AgentContext
from opentf.models.plan import Plan, PlanPhase, PlanStep
from opentf.tools.file_tools import (
    ALL_TOOLS as FILE_TOOLS,
    DEFAULT_SAFE_COMMANDS,
    all_handlers as file_handlers,
)

log = logging.getLogger(__name__)

# Import research tools
try:
    from opentf.agents.specialists.research import TOOLS as RESEARCH_TOOLS, _web_search, _read_url
    _HAS_RESEARCH = True
except ImportError:
    _HAS_RESEARCH = False

PLANNER_PROMPT = """\
You are a planning agent. You create structured execution plans.

You have tools to gather information: web_search, read_url, read_file, list_directory, search_files.
Use them if you need to understand the project or research a topic BEFORE creating the plan.
For simple tasks, skip research and just create the plan directly.

When ready, output your plan as JSON (no other text):
{{
  "label": "short-kebab-case-label",
  "description": "one-line summary",
  "phases": [
    {{
      "name": "Phase Name",
      "description": "what this phase accomplishes",
      "steps": [
        {{
          "id": "1.1",
          "name": "Step name",
          "description": "what this step does",
          "agent_type": "code|file_system|research|data|conversation",
          "depends_on": [],
          "success_criteria": "specific criteria for completion"
        }}
      ]
    }}
  ]
}}

Rules:
- Every step specifies which specialist handles it
- Dependencies reference step IDs
- Steps within a phase can run in parallel if no dependencies
- Each phase completes before the next
- Step IDs: phase_number.step_number (1.1, 1.2, 2.1)
- For simple tasks, keep the plan simple (1-3 steps)"""

ITERATE_PROMPT = """\
Modify this plan based on the user's request.

Current plan:
{plan_json}

User's request: {user_input}

Output the modified plan in the same JSON format. Only change what the user asked for.
Output ONLY the JSON."""


class PlannerAgent(BaseAgent):
    """Planning agent with flat tools. No nested agents, no critique passes."""

    def __init__(self, llm: LLMClient, registry: AgentRegistry) -> None:
        super().__init__(
            name="planner",
            description="Creates structured execution plans with optional research",
            capabilities=["plan", "planner", "planning", "design", "architect"],
            role=AgentRole.SPECIALIST,
        )
        self.llm = llm
        self.registry = registry

    async def process(self, context: AgentContext) -> AgentResult:
        current_plan = context.constraints.get("current_plan")

        if current_plan:
            return await self._iterate_plan(context, current_plan)
        return await self._generate_plan(context)

    async def _generate_plan(self, context: AgentContext) -> AgentResult:
        """Generate plan in a single ToolLoop conversation. No nesting."""
        goal = context.task.description
        bus = context.constraints.get("_bus")

        # Build flat tools: file tools + research tools
        tools = list(FILE_TOOLS)
        handlers = file_handlers(DEFAULT_SAFE_COMMANDS)

        if _HAS_RESEARCH:
            tools.extend(RESEARCH_TOOLS)
            handlers["web_search"] = _web_search
            handlers["read_url"] = _read_url

        loop = ToolLoop(
            llm=self.llm,
            tools=tools,
            handlers=handlers,
            max_iterations=10,
            bus=bus,
            source=self.name,
        )

        messages: list[dict] = []
        for msg in context.conversation_history[-5:]:
            messages.append(msg)
        messages.append({"role": "user", "content": goal})

        text, tokens = await loop.run(
            messages=messages,
            system=PLANNER_PROMPT,
            temperature=0.3,
        )

        plan_data = self._parse_plan_json(text)
        if not plan_data:
            return AgentResult(
                success=False,
                errors=["Failed to generate plan. Try a more specific description."],
                token_usage=tokens,
            )

        plan = self._build_plan(plan_data, goal)

        return AgentResult(
            success=True,
            output={
                "plan": plan.to_dict(),
                "response": plan.format_markdown(),
            },
            token_usage=tokens,
        )

    async def _iterate_plan(
        self, context: AgentContext, current_plan: dict[str, Any],
    ) -> AgentResult:
        """Modify an existing plan. Single LLM call, no tools."""
        response = await self.llm.complete_text(
            messages=[{"role": "user", "content": "Modify this plan."}],
            system=ITERATE_PROMPT.format(
                plan_json=json.dumps(current_plan, indent=2),
                user_input=context.task.description,
            ),
            temperature=0.3,
        )

        plan_data = self._parse_plan_json(response)
        if not plan_data:
            return AgentResult(
                success=False,
                errors=["Failed to modify plan. Try rephrasing your change."],
            )

        plan = self._build_plan(plan_data, current_plan.get("goal", ""))

        return AgentResult(
            success=True,
            output={
                "plan": plan.to_dict(),
                "response": plan.format_markdown(),
            },
        )

    def _build_plan(self, data: dict[str, Any], goal: str) -> Plan:
        phases = []
        for phase_data in data.get("phases", []):
            steps = []
            for step_data in phase_data.get("steps", []):
                steps.append(PlanStep(
                    id=step_data["id"],
                    name=step_data["name"],
                    description=step_data["description"],
                    agent_type=step_data.get("agent_type", "conversation"),
                    depends_on=step_data.get("depends_on", []),
                    success_criteria=step_data.get("success_criteria", ""),
                ))
            phases.append(PlanPhase(
                name=phase_data["name"],
                description=phase_data.get("description", ""),
                steps=steps,
            ))

        return Plan(
            label=data.get("label", "unnamed-plan"),
            description=data.get("description", ""),
            goal=goal,
            phases=phases,
        )

    @staticmethod
    def _parse_plan_json(text: str) -> dict[str, Any] | None:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        try:
            data = json.loads(text)
            if "phases" not in data:
                return None
            return data
        except json.JSONDecodeError:
            return None
