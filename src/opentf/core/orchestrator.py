"""Orchestrator: dispatches to MainAgent. No signal routing.

Single-agent architecture: SecurityAgent gate -> context enrichment ->
MainAgent (one conversation, all tools). Planner, Janitor, and SkillBuilder
are dispatched only by explicit agent_type (from /plan, /janitor commands).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from opentf.agents.base import AgentResult
from opentf.agents.registry import AgentRegistry
from opentf.core.bus import MessageBus
from opentf.models.context import AgentContext
from opentf.models.message import Message, MessageType
from opentf.models.plan import Plan, PlanStep, StepStatus
from opentf.models.task import Task, TaskStatus

log = logging.getLogger(__name__)


class Orchestrator:
    """Single-agent orchestrator. MainAgent handles everything."""

    def __init__(
        self,
        llm: Any,
        registry: AgentRegistry,
        bus: MessageBus,
        prompt_architect: Any = None,  # API compat, unused
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.bus = bus

    async def run(
        self,
        user_input: str,
        conversation_history: list[dict[str, Any]] | None = None,
        on_stream: Any = None,
    ) -> list[AgentResult]:
        history = conversation_history or []

        # 1. Security gate (rule-based, 0 tokens)
        security = self.registry.get("security")
        if security:
            await self._publish_status("security", "checking...")
            sec_ctx = AgentContext(
                task=Task(description=user_input, agent_type="security_check"),
                constraints={"guardrail_phase": "pre"},
            )
            sec_result = await security.process(sec_ctx)
            if not sec_result.success:
                await self._publish_status("security", "blocked", error=True)
                return [sec_result]
            await self._publish_status("security", "passed", done=True)

        # 2. Context enrichment (0 tokens, rule-based)
        enrichments: dict[str, Any] = {}
        context_agent = self.registry.get("context")
        if context_agent:
            ctx = AgentContext(
                task=Task(description=user_input, agent_type="context"),
                conversation_history=history,
            )
            ctx_result = await context_agent.process(ctx)
            if ctx_result.success:
                enrichments.update(ctx_result.output)

        # 3. Token budget (inline math, 0 tokens)
        budget = self._calculate_budget(history)
        enrichments.update(budget)

        # 4. Compaction check
        history_tokens = sum(len(str(m.get("content", ""))) // 4 for m in history)
        if history_tokens > 120_000:
            try:
                from opentf.core.compaction import compact_history
                await self._publish_status("orchestrator", "compacting...")
                history = await compact_history(self.llm, history)
                await self._publish_status("orchestrator", "compacted", done=True)
            except ImportError:
                log.warning("Compaction module not available")

        # 5. Dispatch -- always MainAgent unless explicit agent_type
        agent = self.registry.get("main")
        if not agent:
            return [AgentResult(success=False, errors=["No agent available."])]

        # 6. Execute
        task = Task(description=user_input, agent_type="main")
        constraints = {**enrichments, "_bus": self.bus}
        if on_stream:
            constraints["_on_stream"] = on_stream

        context = AgentContext(
            task=task,
            conversation_history=history,
            constraints=constraints,
        )

        await self._publish_status(agent.name, "thinking...")
        result = await agent.process(context)

        if result.success:
            await self._publish_status(agent.name, "done", done=True)
            await self.bus.publish(Message(
                type=MessageType.TASK_COMPLETED, source=agent.name,
                payload=result.output,
            ))
        else:
            await self._publish_status(agent.name, "failed", error=True)

        return [result]

    def _calculate_budget(self, history: list[dict]) -> dict:
        """Inline token budget. No agent needed."""
        history_tokens = sum(len(str(m.get("content", ""))) // 4 for m in history)
        available = 170_000 - 8192 - 2000 - history_tokens
        max_turns = len(history)
        if available < 10_000:
            max_turns = max(2, len(history) // 2)
        return {"token_budget": max(available, 4000), "max_history_turns": max_turns}

    # --- Plan execution ---

    async def execute_plan(
        self,
        plan: Plan,
        conversation_history: list[dict[str, Any]],
        on_step_update: Any = None,
    ) -> Plan:
        """Execute a confirmed plan phase by phase."""
        plan.status = "executing"
        step_outputs: dict[str, dict] = {}

        for phase in plan.phases:
            await self._publish_status("planner", f"Phase: {phase.name}")

            remaining = list(phase.steps)
            while remaining:
                ready = [s for s in remaining
                         if all(dep in step_outputs for dep in s.depends_on)]
                if not ready:
                    for s in remaining:
                        s.status = StepStatus.FAILED
                        s.error = "Dependency deadlock"
                        if on_step_update:
                            await on_step_update(s)
                    break

                results = await asyncio.gather(
                    *(self._execute_plan_step(s, step_outputs, conversation_history)
                      for s in ready),
                    return_exceptions=True,
                )

                for step, result in zip(ready, results):
                    if isinstance(result, Exception):
                        step.status = StepStatus.FAILED
                        step.error = str(result)
                    elif result.success:
                        step.status = StepStatus.DONE
                        step.output = result.output
                        step_outputs[step.id] = result.output
                    else:
                        step.status = StepStatus.FAILED
                        step.error = "\n".join(result.errors)

                    if on_step_update:
                        await on_step_update(step)
                    remaining.remove(step)

            failed = [s for s in phase.steps if s.status == StepStatus.FAILED]
            if failed:
                for s in remaining:
                    s.status = StepStatus.SKIPPED
                    if on_step_update:
                        await on_step_update(s)
                plan.status = "failed"
                return plan

        plan.status = "completed"
        return plan

    async def _execute_plan_step(
        self, step: PlanStep, prior_outputs: dict, history: list[dict],
    ) -> AgentResult:
        step.status = StepStatus.RUNNING
        await self._publish_status("main", f"step {step.id}: {step.name}")

        # Run through main pipeline
        results = await self.run(step.description, history)
        return results[0] if results else AgentResult(success=False, errors=["No result"])

    # --- Utilities ---

    async def _publish_status(
        self, agent: str, status: str, done: bool = False, error: bool = False,
    ) -> None:
        await self.bus.publish(Message(
            type=MessageType.AGENT_REQUEST,
            source=agent,
            payload={"status": status, "done": done, "error": error},
        ))
