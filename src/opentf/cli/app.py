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
from opentf.cli.theme import COLORS, MODEL_PRICING, SONNET_INPUT_PRICE, SONNET_OUTPUT_PRICE
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
from opentf.core.session import SessionManager
from opentf.llm.client import LLMClient
from opentf.models.janitor import JanitorReport
from opentf.models.message import Message, MessageType
from opentf.models.plan import Plan, StepStatus

_A = COLORS['accent']
_D = COLORS['text_dim']
_B = COLORS['border']
_E = COLORS['error']
_S = COLORS['success']

HELP_TEXT = (
    f"[bold {_A}]{'─' * 40}[/]\n"
    f"  [bold]Commands[/]\n"
    f"[{_B}]{'─' * 40}[/]\n"
    f"  [{_A}]/plan[/]       Planning mode\n"
    f"  [{_A}]/janitor[/]    Code quality scan\n"
    f"  [{_A}]/model[/]      Switch model\n"
    f"  [{_A}]/provider[/]   Switch LLM provider\n"
    f"  [{_A}]/skill[/]      Manage skills\n"
    f"  [{_A}]/undo[/]       Undo last file change\n"
    f"  [{_A}]/review[/]     Toggle file review\n"
    f"  [{_A}]/compact[/]    Compress history\n"
    f"  [{_A}]/resume[/]     Resume session\n"
    f"  [{_A}]/save[/]       Save session\n"
    f"  [{_A}]/sessions[/]   List sessions\n"
    f"  [{_A}]/status[/]     Show status\n"
    f"  [{_A}]/cost[/]       Token usage + cost\n"
    f"  [{_A}]/clear[/]      Clear output\n"
    f"  [{_A}]/exit[/]       Quit\n"
    f"[{_B}]{'─' * 40}[/]\n"
    f"  [{_D}]Esc[/] cancel   [{_D}]Ctrl+C[/] quit   [{_D}]Ctrl+L[/] clear"
)

from opentf.llm.registry import get_models, get_default_model, resolve_model, get_model_short_name

# Legacy compat alias
ANTHROPIC_MODELS = get_models("anthropic")


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
        ("escape", "cancel_or_dismiss", "Cancel / Dismiss"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        from opentf.core.config import load_config
        self._config = load_config()
        llm_cfg = self._config.get("llm", {})

        self.credentials = CredentialManager()
        provider = llm_cfg.get("provider", "anthropic")
        self.llm = LLMClient(
            provider_name=provider,
            model=llm_cfg.get("model", get_default_model(provider)),
            max_tokens=llm_cfg.get("max_tokens", 8192),
            temperature=llm_cfg.get("temperature", 0.7),
        )
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
        self._session_mgr = SessionManager()
        self._janitor_mode: bool = False
        self._janitor_report: JanitorReport | None = None
        self._pending_approval: dict | None = None
        self._active_worker = None
        self._taskforce_pending: bool = False
        self._taskforce_auto: bool = False

        from opentf.core.cost_tracker import CostTracker
        self._cost_tracker = CostTracker()

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        provider = self.llm.provider_name
        needs_key = provider != "ollama"
        has_key = self.credentials.resolve_api_key(provider) if needs_key else True
        if has_key:
            yield from self._compose_main()
        else:
            self._needs_onboarding = True
            yield OnboardingScreen(provider=provider, id="onboarding")

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
            if self._session_mgr.has_current():
                session = self._session_mgr.load()
                count = session["message_count"] if session else 0
                await output.append_text(
                    f"Previous session found ({count} messages). "
                    "Type `/resume` to continue or start fresh."
                )
            else:
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
        self.credentials.store_api_key(event.api_key, provider=event.provider)
        self.llm.api_key = event.api_key
        self.llm.provider_name = event.provider
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
                f"Switched to [bold]{event.short_name}[/] [{_D}]{event.model_id}[/]"
            )
            self.query_one(PromptInput).focus()
        except Exception:
            pass

    def action_cancel_or_dismiss(self) -> None:
        """Cancel active work if running, otherwise dismiss overlays."""
        if self._active_worker and self._active_worker.is_running:
            self._active_worker.cancel()
            self._active_worker = None
            return
        self.action_dismiss_overlays()

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
        """Calculate total estimated cost based on current model."""
        from opentf.cli.widgets.status import _model_short
        short = _model_short(self.llm.model)
        prices = MODEL_PRICING.get(short, MODEL_PRICING["sonnet"])
        u = self.llm.usage
        return (
            u.input_tokens * prices["input"] / 1_000_000
            + u.output_tokens * prices["output"] / 1_000_000
            + u.cache_write_tokens * (prices["input"] * 1.25) / 1_000_000
            + u.cache_read_tokens * (prices["input"] * 0.1) / 1_000_000
        )

    # --- Skill management ---

    async def _handle_skill_command(self, subcmd: str, arg: str) -> None:
        from opentf.core.skill_manager import SkillManager
        manager = SkillManager()
        output = self.query_one(OutputDisplay)

        if subcmd == "list":
            skills = manager.list_skills()
            if not skills:
                await output.append_text("No skills installed.")
                return
            lines = ["[bold]Installed Skills[/]\n"]
            for s in skills:
                tags = ", ".join(s["tags"]) if s["tags"] else ""
                line = (
                    f"  [{_A}]{s['name']}[/]  v{s['version']}"
                    f"  [{_D}]{s['description'][:50]}[/]"
                )
                if tags:
                    line += f"  [{_D}]tags: {tags}[/]"
                lines.append(line)
            await output.append_text("\n".join(lines))

        elif subcmd == "install":
            if not arg:
                await output.append_text(f"Usage: [{_A}]/skill install <url_or_path>[/]")
                return
            ok, msg = await manager.install(arg)
            if ok:
                skill_builder = self.registry.get("skill_builder")
                if skill_builder and hasattr(skill_builder, "load_saved_skills"):
                    skill_builder.load_saved_skills()
                await output.append_text(f"[{_S}]{msg}[/]")
            else:
                await output.append_text(f"[{_E}]{msg}[/]")

        elif subcmd == "export":
            if not arg:
                await output.append_text(f"Usage: [{_A}]/skill export <name>[/]")
                return
            yaml_text = manager.export_skill(arg)
            if yaml_text:
                await output.append_text(f"```yaml\n{yaml_text}```")
            else:
                await output.append_text(f"Skill not found: {arg}")

        elif subcmd == "remove":
            if not arg:
                await output.append_text(f"Usage: [{_A}]/skill remove <name>[/]")
                return
            if manager.remove_skill(arg):
                self.registry.unregister(arg)
                await output.append_text(f"Removed skill '{arg}'.")
            else:
                await output.append_text(f"Skill not found: {arg}")

        else:
            await output.append_text(
                f"Unknown: /skill {subcmd}. "
                f"Try [{_A}]list[/] [{_A}]install[/] [{_A}]export[/] [{_A}]remove[/]"
            )

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
        log_panel.display = True

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
            if source == "main":
                log_panel.display = False

        elif message.type == MessageType.TASK_FAILED:
            log_panel.log_event(source, "failed", "error")
            if source == "main":
                log_panel.display = False

        elif message.type == MessageType.TASK_REJECTED:
            errors = message.payload.get("errors", [])
            log_panel.log_event(source, errors[0][:60] if errors else "rejected", "error")

        elif message.type == MessageType.APPROVAL_REQUESTED:
            cmd = message.payload.get("command", "?")
            tool_id = message.payload.get("tool_id", "")
            diff_text = message.payload.get("diff_text")
            self._pending_approval = {"tool_id": tool_id, "command": cmd}
            cmd_prefix = cmd.strip().split()[0] if cmd.strip() else cmd
            try:
                output = self.query_one(OutputDisplay)
                prompt_line = (
                    f"  [{_S}]y[/] yes   [{_E}]n[/] no   "
                    f"[{_A}]a[/] always allow [{_D}]{cmd_prefix}[/]"
                )
                if diff_text:
                    colored = []
                    for line in diff_text.split("\n"):
                        esc = line.replace("[", "\\[")
                        if line.startswith("+++") or line.startswith("---"):
                            colored.append(f"[bold {_D}]{esc}[/]")
                        elif line.startswith("@@"):
                            colored.append(f"[bold cyan]{esc}[/]")
                        elif line.startswith("+"):
                            colored.append(f"[{_S}]{esc}[/]")
                        elif line.startswith("-"):
                            colored.append(f"[{_E}]{esc}[/]")
                        else:
                            colored.append(f"[{_D}]{esc}[/]")
                    diff_display = "\n".join(colored)
                    self.call_after_refresh(
                        output.append_text,
                        f"\n[bold]File change?[/] [{_D}]{cmd}[/]\n\n"
                        f"{diff_display}\n\n{prompt_line}",
                    )
                else:
                    header = "[bold]Run?[/]"
                    self.call_after_refresh(
                        output.append_text,
                        f"\n{header}\n[{_D}]{cmd}[/]\n\n{prompt_line}",
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
        if self._pending_approval and text.lower() in ("y", "n", "yes", "no", "a", "always"):
            tool_id = self._pending_approval["tool_id"]
            choice = text.lower()
            if choice in ("a", "always"):
                msg_type = MessageType.APPROVAL_ALWAYS
                status = "Always allowed"
            elif choice in ("y", "yes"):
                msg_type = MessageType.APPROVAL_GRANTED
                status = "Approved"
            else:
                msg_type = MessageType.APPROVAL_DENIED
                status = "Skipped"
            await self.bus.publish(Message(
                type=msg_type,
                source="user",
                payload={"tool_id": tool_id},
            ))
            output = self.query_one(OutputDisplay)
            await output.append_text(f"[{_D}]{status}.[/]")
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
        try:
            self.query_one(LogPanel).display = False
        except Exception:
            pass
        await output.add_user_message(text)
        await output.show_thinking()

        self.conversation_history.append({"role": "user", "content": text})
        try:
            self.query_one(HeaderBar).start_timer()
        except Exception:
            pass
        self._active_worker = self._run_pipeline(text)

    async def _handle_command(self, cmd: str) -> None:
        output = self.query_one(OutputDisplay)
        command = cmd.split(maxsplit=1)[0].lower()

        if command == "/plan":
            await self._enter_planning_mode()

        elif command in ("/taskforce", "/tf"):
            parts = cmd.split(maxsplit=1)
            rest = parts[1].strip() if len(parts) > 1 else ""
            auto = rest.startswith("--auto")
            if auto:
                rest = rest[len("--auto"):].strip()
            self._taskforce_pending = True
            self._taskforce_auto = auto
            await self._enter_planning_mode()
            if rest:
                self._run_plan_iteration(rest)

        elif command == "/janitor":
            await self._enter_janitor_mode(cmd)

        elif command == "/resume":
            parts = cmd.split(maxsplit=1)
            label = parts[1].strip() if len(parts) > 1 else "current"
            session = self._session_mgr.load(label)
            if not session and label == "current":
                # Fall back to most recent named session
                sessions = self._session_mgr.list_sessions()
                if sessions:
                    label = sessions[-1]["label"]
                    session = self._session_mgr.load(label)
            if session:
                self.conversation_history[:] = session.get("history", [])
                count = len(self.conversation_history)
                model = session.get("model", "")
                await output.append_text(
                    f"Resumed [bold]{label}[/] [{_D}]({count} messages)[/]"
                    + (f" [{_D}]{model}[/]" if model else "")
                )
            else:
                await output.append_text(f"No saved session. Try [{_A}]/sessions[/]")

        elif command == "/new":
            self.conversation_history.clear()
            self._session_mgr.delete("current")
            await output.clear_output()
            await output.append_text("New session started.")

        elif command == "/sessions":
            sessions = self._session_mgr.list_sessions()
            if not sessions:
                await output.append_text("No saved sessions.")
            else:
                lines = ["[bold]Saved Sessions[/]\n"]
                for s in sessions:
                    lines.append(
                        f"  [{_A}]{s['label']}[/]  {s['message_count']} msgs"
                        f"  [{_D}]{s['saved_at'][:10]}[/]"
                    )
                await output.append_text("\n".join(lines))

        elif command == "/save":
            parts = cmd.split(maxsplit=1)
            label = parts[1].strip().replace(" ", "-").lower() if len(parts) > 1 else "current"
            if not self.conversation_history:
                await output.append_text("Nothing to save -- conversation is empty.")
            else:
                self._session_mgr.save(
                    self.conversation_history, self.llm.model, label=label,
                )
                await output.append_text(f"Session saved as [bold]{label}[/]")

        elif command == "/undo":
            from opentf.tools.file_tools import undo_last
            result = undo_last()
            await output.append_text(result)

        elif command == "/review":
            from opentf.tools.file_tools import get_review_mode, set_review_mode
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                status = "on" if get_review_mode() else "off"
                await output.append_text(f"Review mode: [bold]{status}[/]")
            elif parts[1].strip().lower() == "on":
                set_review_mode(True)
                await output.append_text("Review mode [bold]on[/] -- file changes require approval.")
            elif parts[1].strip().lower() == "off":
                set_review_mode(False)
                await output.append_text("Review mode [bold]off[/] -- file changes auto-accepted.")
            else:
                await output.append_text(f"Usage: [{_A}]/review on[/] or [{_A}]/review off[/]")

        elif command == "/help":
            await output.append_text(HELP_TEXT)

        elif command == "/status":
            provider = self.llm.provider_name
            source = self.credentials.resolve_source(provider)
            key = self.credentials.resolve_api_key(provider)
            redacted = self.credentials.redact_key(key) if key else "(none)"
            await output.append_text(
                f"[bold]Status[/]\n"
                f"  provider [{_D}]{provider}[/]\n"
                f"  auth     [{_D}]{source}[/]\n"
                f"  key      [{_D}]{redacted}[/]\n"
                f"  model    [{_D}]{self.llm.model}[/]\n"
                f"  tokens   [{_D}]{self.llm.usage.total:,}[/]"
            )

        elif command == "/cost":
            from opentf.cli.widgets.status import _model_short
            short = _model_short(self.llm.model)
            prices = MODEL_PRICING.get(short, MODEL_PRICING["sonnet"])
            inp = self.llm.usage.input_tokens
            out = self.llm.usage.output_tokens
            cw = self.llm.usage.cache_write_tokens
            cr = self.llm.usage.cache_read_tokens

            input_cost = inp * prices["input"] / 1_000_000
            output_cost = out * prices["output"] / 1_000_000
            cache_w_cost = cw * (prices["input"] * 1.25) / 1_000_000
            cache_r_cost = cr * (prices["input"] * 0.1) / 1_000_000
            total_cost = input_cost + output_cost + cache_w_cost + cache_r_cost

            cost_lines = [
                f"[bold]Cost[/] [{_D}]({short})[/]",
                f"  input        [{_D}]{inp:,} tokens[/]   ${input_cost:.4f}",
                f"  output       [{_D}]{out:,} tokens[/]   ${output_cost:.4f}",
            ]
            if cw or cr:
                cost_lines.append(f"  cache write  [{_D}]{cw:,} tokens[/]   ${cache_w_cost:.4f}")
                cost_lines.append(f"  cache read   [{_D}]{cr:,} tokens[/]   ${cache_r_cost:.4f}")
            cost_lines.append(f"[{_B}]{'─' * 36}[/]")
            cost_lines.append(f"  [bold]total[/]                    [bold]${total_cost:.4f}[/]")
            await output.append_text("\n".join(cost_lines))

        elif command == "/provider":
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                await output.append_text(
                    f"Current provider: [bold]{self.llm.provider_name}[/]\n"
                    f"  Available: [{_A}]anthropic[/], [{_A}]openai[/], [{_A}]ollama[/]"
                )
            else:
                name = parts[1].strip().lower()
                if name not in ("anthropic", "openai", "ollama"):
                    await output.append_text(
                        f"Unknown provider: {name}. "
                        f"Available: [{_A}]anthropic[/], [{_A}]openai[/], [{_A}]ollama[/]"
                    )
                else:
                    self.llm.provider_name = name
                    self.llm.model = get_default_model(name)
                    self.llm.reset_client()
                    self.query_one(HeaderBar).update_model(self.llm.model)
                    await output.append_text(
                        f"Switched to [bold]{name}[/] (model: {get_model_short_name(self.llm.model)})"
                    )

        elif command == "/model":
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                try:
                    selector = self.query_one(ModelSelector)
                    selector.show(self.llm.model, provider=self.llm.provider_name)
                except Exception:
                    pass
            else:
                name = parts[1].strip().lower()
                provider = self.llm.provider_name
                model_id = resolve_model(provider, name)
                if not model_id:
                    available = get_models(provider)
                    models_list = ", ".join(f"[{_A}]{k}[/]" for k in available)
                    await output.append_text(
                        f"Unknown model: {name}. Available ({provider}): {models_list}"
                    )
                else:
                    self.llm.model = model_id
                    short = get_model_short_name(model_id)
                    self.query_one(HeaderBar).update_model(model_id)
                    await output.append_text(f"Switched to [bold]{short}[/] [{_D}]{model_id}[/]")

        elif command == "/skill":
            parts = cmd.split(maxsplit=2)
            subcmd = parts[1].strip().lower() if len(parts) > 1 else "list"
            arg = parts[2].strip() if len(parts) > 2 else ""
            await self._handle_skill_command(subcmd, arg)

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
                    await output.append_text(f"[{_E}]Error:[/] {exc}")
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
            await self.mount(OnboardingScreen(provider=self.llm.provider_name, id="onboarding"))

        elif command == "/logout":
            self.credentials.clear_credentials()
            await output.append_text(f"Credentials cleared. [{_A}]/login[/] to set a new key.")

        elif command == "/clear":
            self.action_clear()

        elif command == "/exit":
            self.exit()

        else:
            await output.append_text(f"Unknown command: {command}. Try [{_A}]/help[/]")

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
            self._cost_tracker.start_task(self.llm.usage.total, self._calculate_cost())

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
                    self._session_mgr.save(
                        self.conversation_history, self.llm.model,
                    )
                else:
                    error_text = "\n".join(result.errors) or "Unknown error"
                    await output.append_text(f"[{_E}]Error:[/] {error_text}")

            header.stop_timer()
            cost = self._calculate_cost()
            header.update_tokens(self.llm.usage.total)
            header.update_cost(cost)
            header.update_rate(self._cost_tracker.dollars_per_hour(cost))
            self._cost_tracker.end_task(
                user_input[:60], self.llm.usage.total, cost, self.llm.model,
            )

            try:
                self.query_one(ActivityBar).clear_agents()
            except Exception:
                pass

        except asyncio.CancelledError:
            await output.hide_thinking()
            await output.end_stream()
            await output.append_text(f"[{_D}]Cancelled.[/]")
            header.stop_timer()
            try:
                self.query_one(ActivityBar).clear_agents()
            except Exception:
                pass

        except Exception as exc:
            await output.hide_thinking()
            await output.append_text(f"[{_E}]Error:[/] {exc}")
            try:
                header.stop_timer()
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
            f"[bold {_A}]{'─' * 40}[/]\n"
            f"  [bold]Planning Mode[/]\n"
            f"[{_B}]{'─' * 40}[/]\n"
            f"  Describe your goal.\n"
            f"  [{_A}]/confirm[/]  [{_A}]/cancel[/]  [{_A}]/show[/]"
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
            self._plan_store.save(self._current_plan)
            plan = self._current_plan
            self._exit_planning_mode()

            if self._taskforce_pending:
                # Launch taskforce with the confirmed plan
                self._taskforce_pending = False
                await output.append_text("Plan confirmed. Launching task force...")
                self._active_worker = self._launch_taskforce(plan)
            else:
                # Normal plan execution
                await output.append_text("Plan confirmed. Executing...")
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
                f"Unknown: {command}. Try [{_A}]/confirm[/] [{_A}]/cancel[/] [{_A}]/show[/]"
            )

    @work(thread=False)
    async def _run_plan_iteration(self, text: str) -> None:
        output = self.query_one(OutputDisplay)
        activity = self.query_one(ActivityBar)

        activity.clear_agents()
        await output.add_user_message(text)
        await output.show_thinking()
        try:
            self.query_one(HeaderBar).start_timer()
        except Exception:
            pass

        planner = self.registry.get("planner")
        if not planner:
            await output.hide_thinking()
            await output.append_text(f"[{_E}]Error:[/] Planner agent not available.")
            return

        try:
            from opentf.models.task import Task
            from opentf.models.context import AgentContext

            action = "refining plan..." if self._current_plan else "researching..."
            activity.add_agent("Planner", action, "active")

            constraints = {"_bus": self.bus}
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
                    f"[{_D}]Refine, /show, /confirm, or /cancel[/]"
                )
            else:
                error_text = "\n".join(result.errors) or "Unknown error"
                await output.append_text(f"[{_E}]Error:[/] {error_text}")

            header = self.query_one(HeaderBar)
            header.stop_timer()
            header.update_tokens(self.llm.usage.total)

        except Exception as exc:
            await output.hide_thinking()
            await output.append_text(f"[{_E}]Error:[/] {exc}")
        finally:
            try:
                self.query_one(HeaderBar).stop_timer()
                activity.clear_agents()
            except Exception:
                pass

    @work(thread=False)
    async def _execute_plan(self, plan: Plan) -> None:
        output = self.query_one(OutputDisplay)
        header = self.query_one(HeaderBar)
        header.start_timer()

        async def on_step_update(step):
            icon = {
                StepStatus.RUNNING: "[>]",
                StepStatus.DONE: "[x]",
                StepStatus.FAILED: "[!]",
                StepStatus.SKIPPED: "[-]",
            }.get(step.status, "[ ]")

            status_text = ""
            if step.status == StepStatus.DONE:
                status_text = f" [{_S}]done[/]"
            elif step.status == StepStatus.FAILED:
                status_text = f" [{_E}]{step.error[:60]}[/]"
            elif step.status == StepStatus.SKIPPED:
                status_text = f" [{_D}]skipped[/]"

            await output.append_text(
                f"  {icon} [bold]{step.id}[/] {step.name} [{_D}]{step.agent_type}[/]{status_text}"
            )

        try:
            await output.append_text(
                f"[bold {_A}]{'─' * 40}[/]\n"
                f"  [bold]Executing:[/] {plan.label}\n"
                f"[{_B}]{'─' * 40}[/]"
            )

            for phase in plan.phases:
                await output.append_text(f"\n  [bold]{phase.name}[/]")

            result_plan = await self.orchestrator.execute_plan(
                plan,
                conversation_history=self.conversation_history,
                on_step_update=on_step_update,
            )

            header.stop_timer()
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

            summary = result_plan.format_completion_summary()
            await output.append_text(summary)

            self._plan_store.save(result_plan)

        except Exception as exc:
            await output.append_text(f"[{_E}]Error:[/] Plan execution failed: {exc}")
        finally:
            try:
                header.stop_timer()
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
        scope_msg = f" [{_D}]{scope}[/]" if scope else ""
        await output.append_text(
            f"[bold {_A}]{'─' * 40}[/]\n"
            f"  [bold]Janitor Mode[/]{scope_msg}\n"
            f"[{_B}]{'─' * 40}[/]\n"
            f"  Scanning codebase..."
        )
        prompt_widget.focus()
        self._run_janitor_scan(scope)

    # --- Task Force ---

    @work(thread=False)
    async def _launch_taskforce(self, plan: Plan) -> None:
        from opentf.core.taskforce import TaskForce
        from opentf.cli.widgets.taskforce_display import TaskForceDisplay

        header = self.query_one(HeaderBar)
        header.start_timer()

        # Mount dedicated taskforce display, hide normal output
        output = self.query_one(OutputDisplay)
        output.display = False

        tf_display = TaskForceDisplay(
            label=plan.label,
            agents=[],  # Will be populated after blueprint generation
            id="tf-display",
        )
        await self.mount(tf_display, before=self.query_one(PromptInput))

        tf = TaskForce(llm=self.llm, bus=self.bus)

        async def on_status(agent: str, event: str, detail: str) -> None:
            """Update the taskforce display from agent events."""
            try:
                display = self.query_one(TaskForceDisplay)
                if event == "waiting":
                    display.agents.add_agent(agent)
                elif event == "active":
                    display.agents.add_agent(agent)
                    display.agents.set_active(agent, detail)
                    display.live.log_status(agent, detail)
                elif event == "done":
                    display.agents.set_done(agent)
                    display.live.log_status(agent, detail)
                    # Update progress
                    done = sum(1 for c in blueprint.components if c.status == "done") if blueprint else 0
                    total = len(blueprint.components) if blueprint else 0
                    display.header.set_progress(done, total)
                elif event == "failed":
                    display.agents.set_failed(agent, detail)
                    display.live.log_result(agent, "", False, detail)
                elif event == "tool":
                    display.live.log_tool(agent, detail)
                elif event == "result":
                    display.live.log_result(agent, "", True, detail)
            except Exception:
                pass

        # Subscribe to bus for tool events -> live panel
        async def on_bus_tool(msg) -> None:
            try:
                display = self.query_one(TaskForceDisplay)
                if msg.type == MessageType.TOOL_INVOKED:
                    tool = msg.payload.get("tool", "?")
                    summary = msg.payload.get("input_summary", "")[:60]
                    display.live.log_tool(msg.source, tool, summary)
                elif msg.type == MessageType.TOOL_RESULT:
                    tool = msg.payload.get("tool", "?")
                    ok = msg.payload.get("success", True)
                    summary = msg.payload.get("summary", "")[:60]
                    display.live.log_result(msg.source, tool, ok, summary)
            except Exception:
                pass

        self.bus.subscribe(MessageType.TOOL_INVOKED, on_bus_tool)
        self.bus.subscribe(MessageType.TOOL_RESULT, on_bus_tool)

        blueprint = None
        try:
            blueprint = await tf.run(plan, on_status=on_status)
            header.stop_timer()
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except asyncio.CancelledError:
            header.stop_timer()

        except Exception as exc:
            header.stop_timer()
            try:
                self.query_one(TaskForceDisplay).live.log_status("taskforce", f"Error: {exc}")
            except Exception:
                pass

        finally:
            self.bus.unsubscribe(MessageType.TOOL_INVOKED, on_bus_tool)
            self.bus.unsubscribe(MessageType.TOOL_RESULT, on_bus_tool)
            self._taskforce_pending = False
            self._taskforce_auto = False

            # Wait a moment so user can see final state, then restore
            await asyncio.sleep(2)
            try:
                tf_widget = self.query_one(TaskForceDisplay)
                tf_widget.header.stop()
                await tf_widget.remove()
            except Exception:
                pass
            output.display = True

            # Show summary in normal output
            if blueprint:
                await output.append_text(blueprint.format_summary())

            try:
                self.query_one(ActivityBar).clear_agents()
            except Exception:
                pass

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
        header.start_timer()

        janitor = self.registry.get("janitor")
        if not janitor:
            await output.append_text(f"[{_E}]Error:[/] Janitor agent not available.")
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
                        f"[bold]{count} issues found.[/]\n\n"
                        f"  [{_A}]accept N[/]   accept issue J-N\n"
                        f"  [{_A}]reject N[/]   reject issue J-N\n"
                        f"  [{_A}]accept all[/] accept all\n"
                        f"  [{_A}]reject all[/] reject all\n"
                        f"  [{_A}]show N[/]     show detail\n"
                        f"  [{_A}]list[/]       re-display report\n"
                        f"  [{_A}]/done[/]      apply accepted fixes\n"
                        f"  [{_A}]/cancel[/]    discard and exit"
                    )
            else:
                error_text = "\n".join(result.errors) or "Unknown error"
                await output.append_text(f"[{_E}]Error:[/] {error_text}")
                self._exit_janitor_mode()

            header.stop_timer()
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except Exception as exc:
            header.stop_timer()
            await output.append_text(f"[{_E}]Error:[/] {exc}")
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
                await output.append_text(f"Usage: [{_A}]accept N[/] or [{_A}]accept all[/]")

        elif cmd in ("reject", "r"):
            if len(parts) > 1 and parts[1].lower() == "all":
                for issue in report.issues:
                    issue.status = "rejected"
                await output.append_text(f"Rejected all {len(report.issues)} issues.")
            elif len(parts) > 1:
                await self._toggle_issue(report, parts[1], "rejected", output)
            else:
                await output.append_text(f"Usage: [{_A}]reject N[/] or [{_A}]reject all[/]")

        elif cmd == "show":
            if len(parts) > 1:
                await self._show_issue_detail(report, parts[1], output)
            else:
                await output.append_text(f"Usage: [{_A}]show N[/]")

        elif cmd == "list":
            await output.append_text(report.format_markdown())

        else:
            await output.append_text(
                f"Unknown: {text}. Try [{_A}]accept N[/], [{_A}]reject N[/], [{_A}]show N[/], [{_A}]list[/], [{_A}]/done[/], [{_A}]/cancel[/]"
            )

    async def _toggle_issue(
        self, report: JanitorReport, num_str: str, status: str, output: OutputDisplay,
    ) -> None:
        num_str = num_str.lstrip("J-").lstrip("j-")
        try:
            num = int(num_str)
        except ValueError:
            await output.append_text(f"Invalid issue number: {num_str}")
            return

        issue = report.find_issue(num)
        if not issue:
            await output.append_text(f"Issue J-{num} not found.")
            return

        issue.status = status
        icon = "(x)" if status == "accepted" else "(-)"
        await output.append_text(
            f"{icon} [bold]{issue.id}[/] {status} [{_D}]{issue.description[:60]}[/]"
        )

    async def _show_issue_detail(
        self, report: JanitorReport, num_str: str, output: OutputDisplay,
    ) -> None:
        num_str = num_str.lstrip("J-").lstrip("j-")
        try:
            num = int(num_str)
        except ValueError:
            await output.append_text(f"Invalid issue number: {num_str}")
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
                    f"No issues accepted. Use [{_A}]accept N[/] first."
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
        header.start_timer()

        try:
            from opentf.models.task import Task
            from opentf.models.context import AgentContext

            janitor = self.registry.get("janitor")
            if not janitor:
                await output.append_text(f"[{_E}]Error:[/] Janitor agent not available.")
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
                await output.append_text(f"[{_E}]Error:[/] {error_text}")

            header.stop_timer()
            header.update_tokens(self.llm.usage.total)
            header.update_cost(self._calculate_cost())

        except Exception as exc:
            await output.append_text(f"[{_E}]Error:[/] {exc}")
        finally:
            self._exit_janitor_mode()
            try:
                activity.clear_agents()
            except Exception:
                pass


def main() -> None:
    import sys as _sys

    # Headless mode: if --non-interactive/-n or --prompt/-p is given, skip TUI
    if any(
        flag in _sys.argv
        for flag in ("--non-interactive", "-n", "--prompt", "-p")
    ):
        import asyncio as _asyncio
        from opentf.cli.headless import parse_args, run_headless

        args = parse_args()
        exit_code = _asyncio.run(run_headless(args))
        raise SystemExit(exit_code)

    app = OpenTFApp()
    app.run()


if __name__ == "__main__":
    main()
