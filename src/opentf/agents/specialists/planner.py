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

You have tools to gather context: read_file, list_directory, search_files.
For simple tasks, skip tools and output the plan directly.

CRITICAL: When you are ready to present the plan, output ONLY the JSON object.
Do not add any commentary, explanation, or markdown around it. Just the raw JSON.

Step descriptions must be actionable instructions an AI coding agent can execute directly.
Bad:  "Set up the project structure"
Good: "Create src/main.py with a Flask app that has a / route returning Hello World"

JSON format:
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
          "description": "Actionable instruction the executor will follow",
          "agent_type": "code",
          "depends_on": [],
          "success_criteria": "how to verify this step is done"
        }}
      ]
    }}
  ]
}}

Rules:
- For simple tasks: 1 phase, 1-3 steps. Do not over-plan.
- Step descriptions are commands to an executor, not summaries.
- Every step must be self-contained (the executor has no prior context).
- agent_type is always "code" unless the step is purely conversational.
- Step IDs: phase.step (1.1, 1.2, 2.1)
- Dependencies reference step IDs.
- Steps in a phase run in parallel unless they have dependencies."""

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
            log.warning("Plan JSON parse failed. Raw text (%d chars): %s", len(text), text[:500])
            # If the LLM produced text but we couldn't parse JSON from it,
            # return the text as the response so the user sees what happened
            error_msg = "Failed to generate plan."
            if text.strip():
                error_msg += f"\n\nPlanner output:\n{text[:400]}"
            return AgentResult(
                success=False,
                errors=[error_msg],
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

        # Strategy 1: Extract content from any code fence (not just leading)
        import re
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if fence_match:
            fenced = fence_match.group(1).strip()
            try:
                data = json.loads(fenced)
                if "phases" in data:
                    return data
            except json.JSONDecodeError:
                pass

        # Strategy 2: Direct parse of entire text
        try:
            data = json.loads(text)
            if "phases" in data:
                return data
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find JSON by matching braces
        # Walk through the text to find a top-level { and its matching }
        for i, ch in enumerate(text):
            if ch == "{":
                depth = 0
                for j in range(i, len(text)):
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = text[i:j + 1]
                            try:
                                data = json.loads(candidate)
                                if "phases" in data:
                                    return data
                            except json.JSONDecodeError:
                                pass
                            break
        return None
