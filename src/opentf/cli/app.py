"""OpenTF main Textual application."""

from __future__ import annotations

import asyncio
import time

from textual import on, work
from textual.app import App, ComposeResult

from opentf.agents.guardrails.context import ContextAgent
from opentf.agents.guardrails.security import SecurityAgent
from opentf.agents.registry import AgentRegistry
from opentf.agents.specialists.janitor import JanitorAgent
from opentf.agents.specialists.main_agent import MainAgent
from opentf.agents.specialists.planner import PlannerAgent
from opentf.agents.specialists.skill_builder import SkillBuilderAgent
from opentf.auth.credentials import CredentialManager
from opentf.cli.theme import COLORS, SONNET_INPUT_PRICE, SONNET_OUTPUT_PRICE
from opentf.cli.widgets.activity import ActivityBar
from opentf.cli.widgets.command_palette import CommandPalette, CommandSelected
from opentf.cli.widgets.log_panel import LogPanel
from opentf.cli.widgets.model_selector import ModelSelected, ModelSelector
from opentf.cli.widgets.onboarding import OnboardingComplete, OnboardingScreen
from opentf.cli.widgets.output import OutputDisplay
from opentf.cli.widgets.prompt import PromptInput, SlashToggle
from opentf.cli.widgets.status import HeaderBar
from opentf.core.bus import MessageBus
from opentf.core.orchestrator import Orchestrator
from opentf.core.plan_store import PlanStore
from opentf.llm.client import LLMClient
from opentf.models.janitor import JanitorReport
from opentf.models.message import Message, MessageType
from opentf.models.plan import Plan, StepStatus

HELP_TEXT = """\
**Commands:**  `/plan`  `/janitor`  `/model`  `/compact`  `/help`  `/status`  `/cost`  `/login`  `/logout`  `/clear`  `/exit`

**Planning mode:**  `/confirm`  `/cancel`  `/show`

**Janitor mode:**  `/done`  `/cancel`  `accept N`  `reject N`  `list`

**Shortcuts:** Ctrl+C quit, Ctrl+L clear
"""

ANTHROPIC_MODELS = {
    "opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-5-20251001",
}


class OpenTFApp(App):
    """The main OpenTF terminal application."""

    TITLE = "OpenTF"
    CSS = f"""
    Screen {{
        background: {COLORS['bg']};
        layout: vertical;
        layers: default overlay;
    }}
    #header {{
        height: 1;
    }}
    #output {{
        height: 1fr;
    }}
    #log {{
        height: 6;
    }}
    #activity {{
        height: 2;
    }}
    #prompt {{
        height: 3;
    }}
    #palette {{
        layer: overlay;
    }}
    #model-selector {{
        layer: overlay;
    }}
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
        ("escape", "dismiss_overlays", "Dismiss"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.credentials = CredentialManager()
        self.llm = LLMClient()
        self.bus = MessageBus()
        self.registry = AgentRegistry()
        self.orchestrator = Orchestrator(
            llm=self.llm,
            registry=self.registry,
            bus=self.bus,
        )
        self.conversation_history: list[dict] = []
        self._start_time: float = 0.0
        self._needs_onboarding = False
        self._planning_mode: bool = False
        self._current_plan: Plan | None = None
        self._plan_history: list[dict] = []
        self._plan_store = PlanStore()
        self._janitor_mode: bool = False
        self._janitor_report: JanitorReport | None = None
        self._pending_approval: dict | None = None

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        if self.credentials.resolve_api_key():
            yield from self._compose_main()
        else:
            self._needs_onboarding = True
            yield OnboardingScreen(id="onboarding")

    def _compose_main(self) -> ComposeResult:
        yield OutputDisplay(id="output")
        yield LogPanel(id="log")
        yield ActivityBar(id="activity")
        yield CommandPalette(id="palette")
        yield ModelSelector(id="model-selector")
        yield PromptInput(id="prompt")

    def on_mount(self) -> None:
        if not self._needs_onboarding:
            self._setup_main()

    def _setup_main(self) -> None:
        from opentf.core.workspace import scan_workspace
        self._workspace = scan_workspace()
        self._register_agents()
        self.bus.subscribe_all(self._on_bus_message)
        try:
            self.query_one(PromptInput).focus()
        except Exception:
            pass
        self._show_welcome()

    @work(thread=False)
    async def _show_welcome(self) -> None:
        try:
            output = self.query_one(OutputDisplay)
            await output.append_text(
                "Type a message or press `/` for commands."
            )
        except Exception:
            pass

    def _register_agents(self) -> None:
        workspace = getattr(self, "_workspace", None)
        self.registry.register(SecurityAgent())
        self.registry.register(ContextAgent(
            retriever=self._create_retriever(), bus=self.bus, workspace=workspace,
        ))
        self.registry.register(MainAgent(self.llm))
        self.registry.register(JanitorAgent(self.llm))
        self.registry.register(PlannerAgent(self.llm, self.registry))
        skill_builder = SkillBuilderAgent(self.llm, self.registry)
        self.registry.register(skill_builder)
        skill_builder.load_saved_skills()

    def _create_retriever(self) -> object | None:
        try:
            from opentf.memory.retriever import HybridRetriever
            from opentf.memory.store import MemoryStore
            return HybridRetriever(store=MemoryStore())
        except Exception:
            return None

    @on(OnboardingComplete)
    async def on_onboarding_complete(self, event: OnboardingComplete) -> None:
        self.credentials.store_api_key(event.api_key)
        self.llm.api_key = event.api_key
        self.llm.reset_client()

        onboarding = self.query_one("#onboarding")
        await onboarding.remove()
        self._needs_onboarding = False

        await self.mount(OutputDisplay(id="output"))
        await self.mount(LogPanel(id="log"))
        await self.mount(ActivityBar(id="activity"))
        await self.mount(CommandPalette(id="palette"))
        await self.mount(ModelSelector(id="model-selector"))
        await self.mount(PromptInput(id="prompt"))
        self._setup_main()

        output = self.query_one(OutputDisplay)
        await output.append_text("Type a message or press `/` for commands.")

    # --- Overlays: command palette + model selector ---

    @on(SlashToggle)
    def on_slash_toggle(self, event: SlashToggle) -> None:
        """Show/hide palette and filter as user types."""
        try:
            palette = self.query_one(CommandPalette)
            palette.display = event.active
            if event.active:
                # Get the current input value and filter
                prompt = self.query_one(PromptInput)
                query = prompt.value.lstrip("/")
                palette.filter(query)
        except Exception:
            pass

    @on(CommandSelected)
    async def on_command_selected(self, event: CommandSelected) -> None:
        """Handle command selection from palette."""
        try:
            palette = self.query_one(CommandPalette)
            palette.display = False
            prompt = self.query_one(PromptInput)
            prompt.value = ""
        except Exception:
            pass
        await self._handle_command(event.command)

    @on(ModelSelected)
    async def on_model_selected(self, event: ModelSelected) -> None:
        """Handle model selection from interactive selector."""
        self.llm.model = event.model_id
        try:
            header = self.query_one(HeaderBar)
            header.update_model(event.model_id)
            output = self.query_one(OutputDisplay)
            await output.append_text(
                f"Switched to **{event.short_name}** (`{event.model_id}`)"
            )
            self.query_one(PromptInput).focus()
        except Exception:
            pass

    def action_dismiss_overlays(self) -> None:
        """Dismiss any open overlays."""
        try:
            self.query_one(CommandPalette).display = False
        except Exception:
            pass
        try:
            self.query_one(ModelSelector).hide()
        except Exception:
            pass

    # --- Cost helper ---

    def _calculate_cost(self) -> float:
        """Calculate total estimated cost."""
        u = self.llm.usage
        cost = (
            u.input_tokens * SONNET_INPUT_PRICE / 1_000_000
            + u.output_tokens * SONNET_OUTPUT_PRICE / 1_000_000
            + u.cache_write_tokens * (SONNET_INPUT_PRICE * 1.25) / 1_000_000
            + u.cache_read_tokens * (SONNET_INPUT_PRICE * 0.1) / 1_000_000
        )
        return cost

    # --- Bus messages -> widgets ---

    async def _on_bus_message(self, message: Message) -> None:
        try:
            activity = self.query_one(ActivityBar)
            header = self.query_one(HeaderBar)
        except Exception:
            return

        if message.type == MessageType.AGENT_REQUEST:
            status = message.payload.get("status", "")
            done = message.payload.get("done", False)
            error = message.payload.get("error", False)
            name = message.source.title()
            if error:
                activity.fail_agent(name, status)
            elif done:
                activity.complete_agent(name, status)
            else:
                activity.add_agent(name, status, "active")

        elif message.type == MessageType.TASK_CREATED:
            agent_type = message.payload.get("agent_type", "unknown")
            activity.add_agent("Orchestrator", f"-> {agent_type}", "active")

        elif message.type == MessageType.TASK_COMPLETED:
            activity.complete_agent(message.source.title(), "done")

        elif message.type == MessageType.TASK_FAILED:
            activity.fail_agent(message.source.title(), "failed")

        elif message.type == MessageType.TASK_REJECTED:
            errors = message.payload.get("errors", [])
            activity.fail_agent("Validator", errors[0][:40] if errors else "rejected")

        elif message.type == MessageType.TOOL_INVOKED:
            activity.increment_tool_count()

        header.update_tokens(self.llm.usage.total)
        header.update_cost(self._calculate_cost())

        # --- Log panel ---

        try:
            log_panel = self.query_one(LogPanel)
        except Exception:
            return

        source = message.source

        if message.type == MessageType.AGENT_REQUEST:
            status = message.payload.get("status", "")
            error = message.payload.get("error", False)
            done = message.payload.get("done", False)
            style = "error" if error else ("done" if done else "status")
            log_panel.log_event(source, status, style)

        elif message.type == MessageType.TOOL_INVOKED:
            tool = message.payload.get("tool", "?")
            summary = message.payload.get("input_summary", "")[:80]
            log_panel.log_event(source, f"{tool}({summary})", "tool")

        elif message.type == MessageType.TOOL_RESULT:
            tool = message.payload.get("tool", "?")
            ok = message.payload.get("success", True)
            summary = message.payload.get("summary", "")[:60]
            style = "result" if ok else "error"
            log_panel.log_event(source, f"{tool} -> {summary}", style)

        elif message.type == MessageType.TASK_CREATED:
            agent_type = message.payload.get("agent_type", "unknown")
            log_panel.log_event("orchestrator", f"task -> {agent_type}", "status")

        elif message.type == MessageType.TASK_COMPLETED:
            log_panel.log_event(source, "completed", "done")

        elif message.type == MessageType.TASK_FAILED:
            log_panel.log_event(source, "failed", "error")

        elif message.type == MessageType.TASK_REJECTED:
            errors = message.payload.get("errors", [])
            log_panel.log_event(source, errors[0][:60] if errors else "rejected", "error")

        elif message.type == MessageType.APPROVAL_REQUESTED:
            cmd = message.payload.get("command", "?")
            tool_id = message.payload.get("tool_id", "")
            self._pending_approval = {"tool_id": tool_id, "command": cmd}
            try:
                output = self.query_one(OutputDisplay)
                self.call_after_refresh(
                    output.append_text,
                    f"\n**Approve command?** `{cmd}`\n\n  **y** approve  |  **n** deny",
                )
            except Exception:
                pass
            log_panel.log_event(source, f"approval: {cmd[:60]}", "tool")

    # --- Input handling ---

    async def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        if self._needs_onboarding:
            return
        text = event.value.strip()
        if not text:
            return

        # Hide overlays
        try:
            self.query_one(CommandPalette).display = False
        except Exception:
            pass

        prompt_widget = self.query_one(PromptInput)
        prompt_widget.add_to_history(text)
        prompt_widget.value = ""

        # Approval response
        if self._pending_approval and text.lower() in ("y", "n", "yes", "no"):
            tool_id = self._pending_approval["tool_id"]
            approved = text.lower() in ("y", "yes")
            msg_type = MessageType.APPROVAL_GRANTED if approved else MessageType.APPROVAL_DENIED
            await self.bus.publish(Message(
                type=msg_type,
                source="user",
                payload={"tool_id": tool_id},
            ))
            output = self.query_one(OutputDisplay)
            status = "Approved" if approved else "Denied"
            await output.append_text(f"_{status}._")
            self._pending_approval = None
            return

        # Planning mode
        if self._planning_mode:
            if text.startswith("/"):
                await self._handle_plan_command(text)
            else:
                self._run_plan_iteration(text)
            return

        # Janitor mode
        if self._janitor_mode:
            if text.startswith("/"):
                await self._handle_janitor_command(text)
            else:
                await self._handle_janitor_input(text)
            return

        if text.startswith("/"):
            await self._handle_command(text)
            return

        # Normal message
        output = self.query_one(OutputDisplay)
        activity = self.query_one(ActivityBar)

        activity.clear_agents()
        await output.add_user_message(text)
        await output.show_thinking()

        self.conversation_history.append({"role": "user", "content": text})
        self._start_time = time.monotonic()
        self._run_pipeline(text)

    async def _handle_command(self, cmd: str) -> None:
        output = self.query_one(OutputDisplay)
        command = cmd.split(maxsplit=1)[0].lower()

        if command == "/plan":
            await self._enter_planning_mode()

        elif command == "/janitor":
            await self._enter_janitor_mode(cmd)

        elif command == "/help":
            await output.append_text(HELP_TEXT)

        elif command == "/status":
            source = self.credentials.resolve_source()
            key = self.credentials.resolve_api_key()
            redacted = self.credentials.redact_key(key) if key else "(none)"
            await output.append_text(
                f"**Status** -- auth: {source} | key: `{redacted}` | "
                f"model: `{self.llm.model}` | tokens: {self.llm.usage.total:,}"
            )

        elif command == "/cost":
            inp = self.llm.usage.input_tokens
            out = self.llm.usage.output_tokens
            cw = self.llm.usage.cache_write_tokens
            cr = self.llm.usage.cache_read_tokens

            input_cost = inp * SONNET_INPUT_PRICE / 1_000_000
            output_cost = out * SONNET_OUTPUT_PRICE / 1_000_000
            cache_w_cost = cw * (SONNET_INPUT_PRICE * 1.25) / 1_000_000
            cache_r_cost = cr * (SONNET_INPUT_PRICE * 0.1) / 1_000_000
            total_cost = input_cost + output_cost + cache_w_cost + cache_r_cost

            parts = [
                f"input: {inp:,} (${input_cost:.4f})",
                f"output: {out:,} (${output_cost:.4f})",
            ]
            if cw or cr:
                parts.append(f"cache write: {cw:,} (${cache_w_cost:.4f})")
                parts.append(f"cache read: {cr:,} (${cache_r_cost:.4f})")
            await output.append_text(
                f"**Cost** -- {' | '.join(parts)} | total: **${total_cost:.4f}**"
            )

        elif command == "/model":
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                # Show interactive selector
                try:
                    selector = self.query_one(ModelSelector)
                    selector.show(self.llm.model)
                except Exception:
                    pass
            else:
                name = parts[1].strip().lower()
                model_id = ANTHROPIC_MODELS.get(name)
                if not model_id:
                    if name in ANTHROPIC_MODELS.values():
                        model_id = name
                    elif any(name in v for v in ANTHROPIC_MODELS.values()):
                        for v in ANTHROPIC_MODELS.values():
                            if name in v:
                                model_id = v
                                break
                if model_id:
                    self.llm.model = model_id
                    short = next((k for k, v in ANTHROPIC_MODELS.items() if v == model_id), model_id)
                    self.query_one(HeaderBar).update_model(model_id)
                    await output.append_text(f"Switched to **{short}** (`{model_id}`)")
                else:
                    models_list = ", ".join(f"`{k}`" for k in ANTHROPIC_MODELS)
                    await output.append_text(
                        f"Unknown model: `{parts[1].strip()}`. Available: {models_list}"
                    )

        elif command == "/compact":
            if len(self.conversation_history) <= 5:
                await output.append_text("Nothing to compact.")
            else:
                from opentf.core.compaction import compact_history
                before = len(self.conversation_history)
                activity = self.query_one(ActivityBar)
                activity.add_agent("Compactor", "summarizing...", "active")
                try:
                    compacted = await compact_history(self.llm, self.conversation_history)
                    self.conversation_history[:] = compacted
                    after = len(self.conversation_history)
                    await output.append_text(
                        f"Compacted {before} messages into {after} ({before - after} removed)."
                    )
                    activity.complete_agent("Compactor", "done")
                except Exception as exc:
                    await output.append_text(f"**Error:** {exc}")
                    activity.fail_agent("Compactor", "failed")
                finally:
                    activity.clear_agents()

        elif command == "/login":
            for wid in ("output", "activity", "prompt", "palette", "model-selector"):
                try:
                    await self.query_one(f"#{wid}").remove()
                except Exception:
                    pass
            self._needs_onboarding = True
            await self.mount(OnboardingScreen(id="onboarding"))

        elif command == "/logout":
            self.credentials.clear_credentials()
            await output.append_text("Credentials cleared. `/login` to set a new key.")

        elif command == "/clear":
            self.action_clear()

        elif command == "/exit":
            self.exit()

        else:
            await output.append_text(f"Unknown command: `{command}`. Try `/help`.")

    @work(thread=False)
    async def _run_pipeline(self, user_input: str) -> None:
        output = self.query_one(OutputDisplay)
        header = self.query_one(HeaderBar)
        stream_started = False

        async def on_stream(chunk: str) -> None:
            nonlocal stream_started
            if not stream_started:
                await output.hide_thinking()
                await output.start_stream()
                stream_started = True
            await output.append_stream(chunk)

        try:
            results = await self.orchestrator.run(
                user_input,
                conversation_history=self.conversation_history,
                on_stream=on_stream,
            )

            await output.hide_thinking()

            if stream_started:
                await output.end_stream()

            for result in results:
                if result.success:
                    out = result.output
                    streamed = out.get("_streamed", False)
                    if not streamed:
                        if "response" in out:
                            await output.append_text(out["response"])
                        elif "code" in out:
                            lang = out.get("language", "")
                            code = out["code"]
                            explanation = out.get("explanation", "")
                            md = ""
                            if explanation:
                                md += f"{explanation}\n\n"
                            md += f"```{lang}\n{code}\n```"
                            await output.append_text(md)
                        else:
                            await output.append_text(str(out))
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": out.get("response", str(out)),
                    })
                else:
                    error_text = "\n".join(result.errors) or "Unknown error"
                    await output.append_text(f"**Error:** {error_text}")

            elapsed = time.monotonic() - self._start_time
            header.update_elapsed(f"{elapsed:.1f}s")
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

            try:
                self.query_one(ActivityBar).clear_agents()
            except Exception:
                pass

        except Exception as exc:
            await output.hide_thinking()
            await output.append_text(f"**Error:** {exc}")
            try:
                activity = self.query_one(ActivityBar)
                activity.fail_agent("Orchestrator", str(exc)[:40])
                await asyncio.sleep(2)
                activity.clear_agents()
            except Exception:
                pass

    def action_clear(self) -> None:
        try:
            output = self.query_one(OutputDisplay)
            asyncio.ensure_future(output.clear_output())
            activity = self.query_one(ActivityBar)
            activity.clear_agents()
        except Exception:
            pass

    # --- Planning mode ---

    async def _enter_planning_mode(self) -> None:
        self._planning_mode = True
        self._current_plan = None
        self._plan_history = []

        output = self.query_one(OutputDisplay)
        prompt_widget = self.query_one(PromptInput)

        prompt_widget.set_mode("plan")
        await output.append_text(
            "**Planning Mode**\n\n"
            "Describe your goal. The Planner will research, design, and present a plan.\n"
            "Commands: `/confirm` `/cancel` `/show`"
        )
        prompt_widget.focus()

    def _exit_planning_mode(self) -> None:
        self._planning_mode = False
        self._current_plan = None
        self._plan_history = []
        try:
            self.query_one(PromptInput).set_mode("normal")
        except Exception:
            pass

    async def _handle_plan_command(self, cmd: str) -> None:
        output = self.query_one(OutputDisplay)
        command = cmd.split(maxsplit=1)[0].lower()

        if command == "/confirm":
            if not self._current_plan:
                await output.append_text("No plan to confirm. Describe your goal first.")
                return
            self._current_plan.status = "confirmed"
            path = self._plan_store.save(self._current_plan)
            await output.append_text(
                f"Plan saved to `{path}`\n\n**Executing plan...**"
            )
            plan = self._current_plan
            self._exit_planning_mode()
            self._execute_plan(plan)

        elif command in ("/cancel", "/exit"):
            self._exit_planning_mode()
            await output.append_text("Planning cancelled.")

        elif command == "/show":
            if self._current_plan:
                await output.append_text(self._current_plan.format_markdown())
            else:
                await output.append_text("No plan yet. Describe your goal first.")

        else:
            await output.append_text(
                f"Unknown plan command: `{command}`. "
                f"Use `/confirm`, `/cancel`, or `/show`."
            )

    @work(thread=False)
    async def _run_plan_iteration(self, text: str) -> None:
        output = self.query_one(OutputDisplay)
        activity = self.query_one(ActivityBar)

        activity.clear_agents()
        await output.add_user_message(text)
        await output.show_thinking()
        self._start_time = time.monotonic()

        planner = self.registry.get("planner")
        if not planner:
            await output.hide_thinking()
            await output.append_text("**Error:** Planner agent not available.")
            return

        try:
            from opentf.models.task import Task
            from opentf.models.context import AgentContext

            action = "refining plan..." if self._current_plan else "researching..."
            activity.add_agent("Planner", action, "active")

            constraints = {}
            if self._current_plan:
                constraints["current_plan"] = self._current_plan.to_dict()

            task = Task(description=text, agent_type="planner")
            ctx = AgentContext(
                task=task,
                conversation_history=self._plan_history,
                constraints=constraints,
            )

            self._plan_history.append({"role": "user", "content": text})

            result = await planner.process(ctx)

            await output.hide_thinking()
            activity.complete_agent("Planner", "done")

            if result.success:
                plan_data = result.output.get("plan")
                if plan_data:
                    self._current_plan = Plan.from_dict(plan_data)

                response = result.output.get("response", "")
                if response:
                    await output.append_text(response)
                    self._plan_history.append({"role": "assistant", "content": response})

                await output.append_text(
                    "_Type changes to refine, `/show` to re-display, "
                    "`/confirm` to execute, `/cancel` to discard._"
                )
            else:
                error_text = "\n".join(result.errors) or "Unknown error"
                await output.append_text(f"**Error:** {error_text}")

            elapsed = time.monotonic() - self._start_time
            self.query_one(HeaderBar).update_elapsed(f"{elapsed:.1f}s")
            self.query_one(HeaderBar).update_tokens(self.llm.usage.total)

        except Exception as exc:
            await output.hide_thinking()
            await output.append_text(f"**Error:** {exc}")
        finally:
            try:
                activity.clear_agents()
            except Exception:
                pass

    @work(thread=False)
    async def _execute_plan(self, plan: Plan) -> None:
        output = self.query_one(OutputDisplay)
        header = self.query_one(HeaderBar)
        self._start_time = time.monotonic()

        async def on_step_update(step):
            icon = {
                StepStatus.RUNNING: "[>]",
                StepStatus.DONE: "[x]",
                StepStatus.FAILED: "[!]",
                StepStatus.SKIPPED: "[-]",
            }.get(step.status, "[ ]")

            status_text = ""
            if step.status == StepStatus.DONE:
                status_text = " -- done"
            elif step.status == StepStatus.FAILED:
                status_text = f" -- failed: {step.error[:60]}"
            elif step.status == StepStatus.SKIPPED:
                status_text = " -- skipped"

            await output.append_text(
                f"  {icon} **{step.id}** {step.name} (`{step.agent_type}`){status_text}"
            )

        try:
            await output.append_text(f"## Executing: {plan.label}")

            for phase in plan.phases:
                await output.append_text(f"### {phase.name}")

            result_plan = await self.orchestrator.execute_plan(
                plan,
                conversation_history=self.conversation_history,
                on_step_update=on_step_update,
            )

            elapsed = time.monotonic() - self._start_time

            if result_plan.status == "completed":
                await output.append_text(f"**Plan completed** in {elapsed:.1f}s")
            else:
                await output.append_text(f"**Plan failed** after {elapsed:.1f}s")

            self._plan_store.save(result_plan)

            header.update_elapsed(f"{elapsed:.1f}s")
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except Exception as exc:
            await output.append_text(f"**Error during plan execution:** {exc}")
        finally:
            try:
                self.query_one(ActivityBar).clear_agents()
            except Exception:
                pass

    # --- Janitor mode ---

    async def _enter_janitor_mode(self, cmd: str) -> None:
        parts = cmd.split(maxsplit=1)
        scope = parts[1].strip() if len(parts) > 1 else None

        self._janitor_mode = True
        self._janitor_report = None

        output = self.query_one(OutputDisplay)
        prompt_widget = self.query_one(PromptInput)

        prompt_widget.set_mode("janitor")
        scope_msg = f" (scope: `{scope}`)" if scope else ""
        await output.append_text(
            f"**Janitor Mode**{scope_msg}\n\nScanning codebase for issues..."
        )
        prompt_widget.focus()
        self._run_janitor_scan(scope)

    def _exit_janitor_mode(self) -> None:
        self._janitor_mode = False
        self._janitor_report = None
        try:
            self.query_one(PromptInput).set_mode("normal")
        except Exception:
            pass

    @work(thread=False)
    async def _run_janitor_scan(self, scope: str | None) -> None:
        output = self.query_one(OutputDisplay)
        activity = self.query_one(ActivityBar)
        header = self.query_one(HeaderBar)

        activity.clear_agents()
        activity.add_agent("Janitor", "scanning...", "active")
        self._start_time = time.monotonic()

        janitor = self.registry.get("janitor")
        if not janitor:
            await output.append_text("**Error:** Janitor agent not available.")
            self._exit_janitor_mode()
            return

        try:
            from opentf.models.task import Task
            from opentf.models.context import AgentContext

            constraints: dict = {"_bus": self.bus}
            if scope:
                constraints["scan_scope"] = scope

            task = Task(description="Scan codebase for issues", agent_type="janitor")
            ctx = AgentContext(task=task, conversation_history=[], constraints=constraints)

            result = await janitor.process(ctx)

            activity.complete_agent("Janitor", "done")

            if result.success:
                report_data = result.output.get("report")
                if report_data:
                    self._janitor_report = JanitorReport.from_dict(report_data)

                response = result.output.get("response", "")
                if response:
                    await output.append_text(response)

                count = len(self._janitor_report.issues) if self._janitor_report else 0
                if count == 0:
                    await output.append_text("No issues found.")
                    self._exit_janitor_mode()
                else:
                    await output.append_text(
                        f"**{count} issues found.** Review commands:\n\n"
                        "  `accept N` or `a N` -- accept issue J-N\n"
                        "  `reject N` or `r N` -- reject issue J-N\n"
                        "  `accept all` -- accept all issues\n"
                        "  `reject all` -- reject all issues\n"
                        "  `show N` -- show detail for issue J-N\n"
                        "  `list` -- re-display the report\n"
                        "  `/done` -- apply accepted fixes\n"
                        "  `/cancel` -- discard and exit"
                    )
            else:
                error_text = "\n".join(result.errors) or "Unknown error"
                await output.append_text(f"**Error:** {error_text}")
                self._exit_janitor_mode()

            elapsed = time.monotonic() - self._start_time
            header.update_elapsed(f"{elapsed:.1f}s")
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except Exception as exc:
            await output.append_text(f"**Error:** {exc}")
            self._exit_janitor_mode()
        finally:
            try:
                activity.clear_agents()
            except Exception:
                pass

    async def _handle_janitor_input(self, text: str) -> None:
        output = self.query_one(OutputDisplay)
        report = self._janitor_report

        if not report:
            await output.append_text("No report loaded. Wait for scan to finish.")
            return

        parts = text.strip().split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("accept", "a"):
            if len(parts) > 1 and parts[1].lower() == "all":
                for issue in report.issues:
                    issue.status = "accepted"
                await output.append_text(f"Accepted all {len(report.issues)} issues.")
            elif len(parts) > 1:
                await self._toggle_issue(report, parts[1], "accepted", output)
            else:
                await output.append_text("Usage: `accept N` or `accept all`")

        elif cmd in ("reject", "r"):
            if len(parts) > 1 and parts[1].lower() == "all":
                for issue in report.issues:
                    issue.status = "rejected"
                await output.append_text(f"Rejected all {len(report.issues)} issues.")
            elif len(parts) > 1:
                await self._toggle_issue(report, parts[1], "rejected", output)
            else:
                await output.append_text("Usage: `reject N` or `reject all`")

        elif cmd == "show":
            if len(parts) > 1:
                await self._show_issue_detail(report, parts[1], output)
            else:
                await output.append_text("Usage: `show N`")

        elif cmd == "list":
            await output.append_text(report.format_markdown())

        else:
            await output.append_text(
                f"Unknown command: `{text}`. "
                "Try `accept N`, `reject N`, `show N`, `list`, `/done`, or `/cancel`."
            )

    async def _toggle_issue(
        self, report: JanitorReport, num_str: str, status: str, output: OutputDisplay,
    ) -> None:
        num_str = num_str.lstrip("J-").lstrip("j-")
        try:
            num = int(num_str)
        except ValueError:
            await output.append_text(f"Invalid issue number: `{num_str}`")
            return

        issue = report.find_issue(num)
        if not issue:
            await output.append_text(f"Issue J-{num} not found.")
            return

        issue.status = status
        icon = "(x)" if status == "accepted" else "(-)"
        await output.append_text(
            f"{icon} **[{issue.id}]** {status} -- {issue.description[:60]}"
        )

    async def _show_issue_detail(
        self, report: JanitorReport, num_str: str, output: OutputDisplay,
    ) -> None:
        num_str = num_str.lstrip("J-").lstrip("j-")
        try:
            num = int(num_str)
        except ValueError:
            await output.append_text(f"Invalid issue number: `{num_str}`")
            return

        issue = report.find_issue(num)
        if not issue:
            await output.append_text(f"Issue J-{num} not found.")
            return

        await output.append_text(issue.detail_block())

    async def _handle_janitor_command(self, cmd: str) -> None:
        output = self.query_one(OutputDisplay)
        command = cmd.split(maxsplit=1)[0].lower()

        if command == "/done":
            if not self._janitor_report:
                await output.append_text("No report to apply.")
                return

            accepted = self._janitor_report.accepted_issues()
            if not accepted:
                await output.append_text(
                    "No issues accepted. Use `accept N` to accept issues first."
                )
                return

            await output.append_text(f"Applying {len(accepted)} accepted fixes...")
            self._apply_janitor_fixes(accepted)

        elif command in ("/cancel", "/exit"):
            self._exit_janitor_mode()
            await output.append_text("Janitor mode cancelled.")

        elif command == "/show":
            if self._janitor_report:
                await output.append_text(self._janitor_report.format_markdown())
            else:
                await output.append_text("No report yet.")

        else:
            await output.append_text(
                f"Unknown janitor command: `{command}`. "
                "Use `/done`, `/cancel`, or `/show`."
            )

    @work(thread=False)
    async def _apply_janitor_fixes(self, issues: list) -> None:
        output = self.query_one(OutputDisplay)
        activity = self.query_one(ActivityBar)
        header = self.query_one(HeaderBar)

        activity.clear_agents()
        activity.add_agent("Janitor", "applying fixes...", "active")
        self._start_time = time.monotonic()

        try:
            from opentf.models.task import Task
            from opentf.models.context import AgentContext

            janitor = self.registry.get("janitor")
            if not janitor:
                await output.append_text("**Error:** Janitor agent not available.")
                return

            issues_data = []
            for issue in issues:
                issues_data.append({
                    "id": issue.id,
                    "file_path": issue.file_path,
                    "line_start": issue.line_start,
                    "line_end": issue.line_end,
                    "description": issue.description,
                    "suggested_fix": issue.suggested_fix,
                })

            task = Task(description="Apply accepted janitor fixes", agent_type="janitor")
            ctx = AgentContext(
                task=task,
                conversation_history=[],
                constraints={
                    "mode": "fix",
                    "issues": issues_data,
                    "_bus": self.bus,
                },
            )

            result = await janitor.process(ctx)

            activity.complete_agent("Janitor", "done")

            if result.success:
                response = result.output.get("response", "Fixes applied.")
                await output.append_text(response)
            else:
                error_text = "\n".join(result.errors) or "Unknown error"
                await output.append_text(f"**Error:** {error_text}")

            elapsed = time.monotonic() - self._start_time
            header.update_elapsed(f"{elapsed:.1f}s")
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except Exception as exc:
            await output.append_text(f"**Error:** {exc}")
        finally:
            self._exit_janitor_mode()
            try:
                activity.clear_agents()
            except Exception:
                pass


def main() -> None:
    app = OpenTFApp()
    app.run()


if __name__ == "__main__":
    main()
