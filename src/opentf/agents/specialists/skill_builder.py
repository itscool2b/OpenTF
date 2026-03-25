"""Skill Builder -- the meta-agent.

Creates new specialist agents at runtime by generating optimized system
prompts and registering them as SkillAgent instances. Prompt-based agents,
NOT code generation -- safer, simpler, still powerful.

Every generated SkillAgent returns a universal output format:
{"response": text}. No custom output per skill.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from opentf.agents.base import AgentResult, AgentRole, BaseAgent
from opentf.agents.registry import AgentRegistry
from opentf.llm.client import LLMClient
from opentf.models.context import AgentContext

log = logging.getLogger(__name__)

SKILLS_DIR = Path.home() / ".config" / "opentf" / "skills"

BUILDER_PROMPT = """\
You are the Skill Builder. Your job is to design a new specialist agent.

The user wants an agent that can handle a specific type of task. Design one by
producing a JSON object with:

{{
  "name": "short_snake_case_identifier (unique, descriptive)",
  "description": "one-line description of what this agent does (used for routing)",
  "capabilities": ["keyword1", "keyword2", "keyword3"],
  "system_prompt": "the full system prompt that defines the agent's behavior"
}}

Rules for the system_prompt:
- Tell the agent to respond in plain text/markdown (NOT JSON)
- Be thorough and specific about what the agent does
- Include guidelines for handling edge cases
- Include 1-2 examples of good output style
- The agent will receive user requests and conversation history
- Keep the prompt focused -- one clear purpose

Rules for capabilities:
- 3-6 keywords that describe what tasks should route to this agent
- These are used for automatic routing -- choose descriptive, non-overlapping words
- Don't overlap with existing agents: code, conversation, research, file_system, data

Respond with ONLY the JSON object. No other text."""


class SkillAgent(BaseAgent):
    """Dynamically created specialist. Universal output: {"response": text}."""

    def __init__(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        system_prompt: str,
        llm: LLMClient,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            capabilities=capabilities,
            role=AgentRole.SPECIALIST,
        )
        self.system_prompt = system_prompt
        self.llm = llm

    async def process(self, context: AgentContext) -> AgentResult:
        messages: list[dict] = []
        for msg in context.conversation_history[-10:]:
            messages.append(msg)
        messages.append({"role": "user", "content": context.task.description})

        response = await self.llm.complete(
            messages=messages,
            system=self.system_prompt,
            temperature=0.5,
        )
        text = response.content[0].text  # type: ignore[union-attr]
        tokens = response.usage.input_tokens + response.usage.output_tokens

        return AgentResult(
            success=True,
            output={"response": text},
            token_usage=tokens,
        )


class SkillBuilderAgent(BaseAgent):
    """Meta-agent that creates new SkillAgents at runtime."""

    def __init__(self, llm: LLMClient, registry: AgentRegistry) -> None:
        super().__init__(
            name="skill_builder",
            description="Creates new specialist agents on demand -- the meta-agent",
            capabilities=["skill", "create_agent", "build_skill", "meta",
                          "new_agent", "custom_agent"],
            role=AgentRole.SPECIALIST,
        )
        self.llm = llm
        self.registry = registry

    async def process(self, context: AgentContext) -> AgentResult:
        """Generate a new SkillAgent from the user's description."""
        messages = [{"role": "user", "content": context.task.description}]

        response = await self.llm.complete_text(
            messages=messages,
            system=BUILDER_PROMPT,
            temperature=0.5,
        )
        tokens = 0  # tracked by LLMClient globally

        # Parse the generated agent spec
        spec = self._parse_spec(response)
        if not spec:
            return AgentResult(
                success=False,
                errors=["Failed to generate agent specification. Try a more specific description."],
            )

        name = spec["name"]
        description = spec["description"]
        capabilities = spec["capabilities"]
        system_prompt = spec["system_prompt"]

        # Security check: validate the generated system prompt
        security = self.registry.get("security")
        if security:
            from opentf.models.task import Task
            check_task = Task(description=system_prompt, agent_type="security_check")
            check_ctx = AgentContext(task=check_task, constraints={"guardrail_phase": "pre"})
            sec_result = await security.process(check_ctx)
            if not sec_result.success:
                return AgentResult(
                    success=False,
                    errors=[f"Generated skill blocked by security: {sec_result.errors}"],
                )

        # Check for name collision
        existing = self.registry.get(name)
        if existing:
            name = f"{name}_custom"

        # Create and register the new agent
        skill = SkillAgent(
            name=name,
            description=description,
            capabilities=capabilities,
            system_prompt=system_prompt,
            llm=self.llm,
        )
        self.registry.register(skill)

        # Persist to disk
        self._save_skill(name, description, capabilities, system_prompt)

        return AgentResult(
            success=True,
            output={
                "response": (
                    f"Created new agent **{name}**.\n\n"
                    f"**Description:** {description}\n\n"
                    f"**Routes on:** {', '.join(capabilities)}\n\n"
                    f"The agent is now active and will handle matching requests automatically."
                ),
            },
        )

    def load_saved_skills(self) -> int:
        """Load persisted skills from disk. Returns count loaded."""
        if not SKILLS_DIR.exists():
            return 0

        count = 0
        for path in SKILLS_DIR.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text())
                if not data or not isinstance(data, dict):
                    continue

                skill = SkillAgent(
                    name=data["name"],
                    description=data["description"],
                    capabilities=data["capabilities"],
                    system_prompt=data["system_prompt"],
                    llm=self.llm,
                )
                self.registry.register(skill)
                count += 1
                log.info("Loaded saved skill: %s", data["name"])
            except Exception as exc:
                log.warning("Failed to load skill %s: %s", path.name, exc)

        return count

    def _save_skill(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        system_prompt: str,
    ) -> None:
        """Persist a skill to disk as YAML."""
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        path = SKILLS_DIR / f"{name}.yaml"
        data = {
            "name": name,
            "description": description,
            "capabilities": capabilities,
            "system_prompt": system_prompt,
        }
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        log.info("Saved skill to %s", path)

    @staticmethod
    def _parse_spec(text: str) -> dict[str, Any] | None:
        """Parse the LLM's response into a skill spec."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        try:
            data = json.loads(text)
            # Validate required fields
            required = ["name", "description", "capabilities", "system_prompt"]
            if not all(k in data for k in required):
                return None
            if not isinstance(data["capabilities"], list):
                return None
            return data
        except (json.JSONDecodeError, KeyError):
            return None
