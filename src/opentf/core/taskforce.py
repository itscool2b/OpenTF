"""TaskForce orchestrator -- parallel specialist agent swarm.

Takes a confirmed Plan, converts it to a Blueprint with specialist agents,
executes specialists in parallel waves, integrates, and validates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Awaitable

from opentf.core.bus import MessageBus
from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.models.blueprint import Blueprint, Component, Specialist
from opentf.models.message import Message, MessageType
from opentf.models.plan import Plan
from opentf.tools.file_tools import (
    ALL_TOOLS as FILE_TOOLS,
    CODE_SAFE_COMMANDS,
    all_handlers as file_handlers,
)

log = logging.getLogger(__name__)

StatusFn = Callable[[str, str, str], Awaitable[None]]
# StatusFn(agent_name, event_type, detail)
# event_type: "active", "done", "failed", "tool", "result"

BLUEPRINT_PROMPT = """\
You are the Task Force Architect. Convert this confirmed execution plan into \
specialist agent definitions.

Confirmed plan:
{plan_json}

For each phase/step group, create a specialist agent with a focused system prompt.

CRITICAL: Output ONLY the JSON object.

JSON format:
{{
  "specialists": [
    {{
      "name": "short_snake_name",
      "role": "One-line role description",
      "scope": "What files/directories this specialist works on",
      "system_prompt": "Detailed system prompt telling the agent exactly what to build. \
Include file paths, function signatures, coding style, and error handling expectations.",
      "steps": ["1.1", "1.2"]
    }}
  ],
  "waves": [["specialist_names_wave_1"], ["wave_2"]]
}}

Rules:
- Group related plan steps into specialists (1 specialist can handle multiple steps)
- 2-6 specialists total
- Waves respect step dependencies from the plan
- Independent specialists go in the same wave (parallel execution)
- System prompts must be detailed and self-contained
- Include an integration/test specialist if the plan has test steps"""

INTEGRATE_PROMPT = """\
You are an integration specialist. Review all files created by the task force \
and fix any issues:

1. Resolve import conflicts and missing references
2. Wire entry points and configuration together
3. Ensure the project runs as a cohesive whole

Use read_file to inspect files, then edit_file to fix issues.
Report what you fixed."""

VALIDATE_PROMPT = """\
You are a validation specialist. Run the project's tests and report results.
If tests don't exist yet, create basic smoke tests.
Use run_command to execute the test suite.
Report: what passed, what failed, and any issues found."""


class TaskForce:
    """Orchestrates parallel specialist agents from a confirmed Plan."""

    def __init__(self, llm: LLMClient, bus: MessageBus) -> None:
        self.llm = llm
        self.bus = bus

    async def run(
        self,
        plan: Plan,
        on_status: StatusFn | None = None,
    ) -> Blueprint:
        """Execute a confirmed plan as a parallel task force.

        on_status(agent, event, detail) is called for live UI updates.
        """
        self._on_status = on_status or self._noop_status

        # Convert plan to blueprint (1 LLM call)
        await self._notify("taskforce", "active", "generating specialists...")
        blueprint = await self._plan_to_blueprint(plan)
        await self._notify("taskforce", "done", f"{len(blueprint.specialists)} specialists ready")

        # Notify UI about all agents
        for spec in blueprint.specialists:
            await self._notify(spec.name, "waiting", "queued")

        # Execute waves in order
        for wave_idx, wave in enumerate(blueprint.waves):
            specs = [s for s in blueprint.specialists if s.name in wave]
            await self._execute_wave(specs, blueprint, wave_idx)

        # Integration pass
        await self._notify("integrator", "active", "resolving conflicts...")
        await self._integrate(blueprint)
        await self._notify("integrator", "done", "integration complete")

        # Validation pass
        await self._notify("validator", "active", "running tests...")
        await self._validate(blueprint)
        await self._notify("validator", "done", "validation complete")

        # Security review (always runs last -- deterministic, no LLM)
        await self._notify("security", "active", "scanning for vulnerabilities...")
        security_findings = await self._security_review(blueprint)
        if security_findings:
            await self._notify("security", "done", f"{len(security_findings)} findings")
            blueprint.security_findings = security_findings
        else:
            await self._notify("security", "done", "no issues found")

        blueprint.status = "completed"
        return blueprint

    # --- Blueprint generation ---

    async def _plan_to_blueprint(self, plan: Plan) -> Blueprint:
        """Convert a confirmed Plan into a Blueprint with specialists."""
        plan_json = json.dumps(plan.to_dict(), indent=2)

        response = await self.llm.complete_text(
            messages=[{"role": "user", "content": f"Create specialists for this plan:\n{plan_json}"}],
            system=BLUEPRINT_PROMPT.format(plan_json=plan_json),
            temperature=0.3,
        )

        data = self._parse_json(response)
        if not data or "specialists" not in data:
            log.warning("Blueprint parse failed: %s", response[:500])
            raise ValueError("Failed to generate task force blueprint")

        # Build components from plan steps
        components = []
        for phase in plan.phases:
            for step in phase.steps:
                components.append(Component(
                    name=step.id,
                    description=step.description,
                    specialist=self._find_specialist_for_step(step.id, data["specialists"]),
                    depends_on=step.depends_on,
                ))

        specialists = [
            Specialist(
                name=s["name"],
                role=s.get("role", ""),
                scope=s.get("scope", ""),
                system_prompt=s.get("system_prompt", ""),
            )
            for s in data["specialists"]
        ]

        waves = data.get("waves", [[s["name"] for s in data["specialists"]]])

        return Blueprint(
            goal=plan.goal,
            components=components,
            specialists=specialists,
            waves=waves,
        )

    @staticmethod
    def _find_specialist_for_step(step_id: str, specialists: list[dict]) -> str:
        """Find which specialist handles a given step."""
        for spec in specialists:
            if step_id in spec.get("steps", []):
                return spec["name"]
        # Default to first specialist
        return specialists[0]["name"] if specialists else "unknown"

    # --- Execution ---

    async def _execute_wave(
        self, specialists: list[Specialist], blueprint: Blueprint, wave_idx: int,
    ) -> None:
        """Run specialists in this wave in parallel."""
        tasks = []
        for spec in specialists:
            components = [c for c in blueprint.components if c.specialist == spec.name]
            if not components:
                continue
            for comp in components:
                comp.status = "running"
            description = "\n\n".join(
                f"Step {c.name}: {c.description}" for c in components
            )
            tasks.append(self._run_specialist(spec, description))

        results = await asyncio.gather(*tasks, return_exceptions=True)  # type: ignore[arg-type]

        for spec, result in zip(specialists, results):
            components = [c for c in blueprint.components if c.specialist == spec.name]
            if isinstance(result, Exception):
                for comp in components:
                    comp.status = "failed"
                await self._notify(spec.name, "failed", str(result)[:60])
            else:
                for comp in components:
                    comp.status = "done"
                await self._notify(spec.name, "done", "complete")

    async def _run_specialist(self, spec: Specialist, description: str) -> str:
        """Run a single specialist agent with tool access."""
        await self._notify(spec.name, "active", "working...")

        loop = ToolLoop(
            llm=self.llm,
            tools=list(FILE_TOOLS),
            handlers=file_handlers(CODE_SAFE_COMMANDS),
            max_iterations=25,
            bus=self.bus,
            source=spec.name,
        )

        messages = [{"role": "user", "content": description}]
        text, tokens = await loop.run(
            messages=messages,
            system=spec.system_prompt,
            temperature=0.3,
        )
        return text

    # --- Integration & Validation ---

    async def _integrate(self, blueprint: Blueprint) -> None:
        loop = ToolLoop(
            llm=self.llm,
            tools=list(FILE_TOOLS),
            handlers=file_handlers(CODE_SAFE_COMMANDS),
            max_iterations=15,
            bus=self.bus,
            source="integrator",
        )
        component_list = "\n".join(f"- {c.name}: {c.description[:80]}" for c in blueprint.components)
        messages = [{"role": "user", "content": f"Integrate these components:\n{component_list}"}]
        await loop.run(messages=messages, system=INTEGRATE_PROMPT, temperature=0.3)

    async def _validate(self, blueprint: Blueprint) -> None:
        loop = ToolLoop(
            llm=self.llm,
            tools=list(FILE_TOOLS),
            handlers=file_handlers(CODE_SAFE_COMMANDS),
            max_iterations=15,
            bus=self.bus,
            source="validator",
        )
        messages = [{"role": "user", "content": "Validate the project. Run tests and report results."}]
        await loop.run(messages=messages, system=VALIDATE_PROMPT, temperature=0.3)

    async def _security_review(self, blueprint: Blueprint) -> list[str]:
        """Deterministic security review of all files touched during taskforce.

        Scans for: hardcoded secrets, command injection, SQL injection, XSS,
        eval/exec, insecure imports, path traversal. No LLM needed.
        """
        from opentf.agents.guardrails.security import scan_file_content
        from pathlib import Path

        # Collect files touched by scanning component outputs
        touched_files: set[str] = set()
        pattern = re.compile(r"(?:Edited|Written|Created|Applied)\s+(\S+)")
        for comp in blueprint.components:
            if comp.output:
                for match in pattern.finditer(comp.output):
                    touched_files.add(match.group(1))

        # Also scan all files in the working directory that were recently modified
        # (within the last 10 minutes -- covers taskforce execution window)
        import time
        cutoff = time.time() - 600
        try:
            for p in Path.cwd().rglob("*"):
                if p.is_file() and p.stat().st_mtime > cutoff and not any(
                    seg in str(p) for seg in (".git", "__pycache__", ".opentf", "node_modules")
                ):
                    touched_files.add(str(p))
        except Exception:
            pass

        findings: list[str] = []
        for fpath in sorted(touched_files):
            try:
                path = Path(fpath)
                if not path.exists() or not path.is_file():
                    continue
                if path.stat().st_size > 500_000:
                    continue  # Skip very large files
                content = path.read_text(errors="replace")
                file_findings = scan_file_content(content, str(path))
                findings.extend(file_findings)
            except Exception:
                continue

        return findings

    # --- Utilities ---

    async def _notify(self, agent: str, event: str, detail: str) -> None:
        if self._on_status:
            await self._on_status(agent, event, detail)
        await self.bus.publish(Message(
            type=MessageType.AGENT_REQUEST,
            source=agent,
            payload={"status": detail, "done": event == "done", "error": event == "failed"},
        ))

    @staticmethod
    async def _noop_status(agent: str, event: str, detail: str) -> None:
        pass

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        text = text.strip()
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        for i, ch in enumerate(text):
            if ch == "{":
                depth = 0
                for j in range(i, len(text)):
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(text[i:j + 1])
                            except json.JSONDecodeError:
                                pass
                            break
        return None
