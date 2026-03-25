"""Interactive REPL mode -- the default CLI experience.

Prints inline in the terminal like Claude Code or OpenCode.
Text is fully selectable and copyable. No Textual TUI.

Usage:
    opentf              # launches REPL
    opentf --tui        # launches full Textual TUI instead
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import sys
import signal
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.theme import Theme

from opentf.cli.theme import (
    COLORS, MODEL_PRICING, available_themes, get_theme, set_theme,
)
from opentf.core.cost_tracker import CostTracker
from opentf.core.engine import Engine
from opentf.llm.registry import (
    get_default_model, get_model_short_name, get_models,
    get_model_pricing, resolve_model,
)
from opentf.models.message import Message, MessageType

console = Console(highlight=False)

# Styles derived from active theme
def _styled(text: str, color: str) -> str:
    return f"[{color}]{text}[/]"


def _print_banner(provider: str, model: str) -> None:
    short = get_model_short_name(model)
    console.print(
        f"\n[bold {COLORS['accent']}]OpenTF[/] v0.1.0 "
        f"[{COLORS['text_dim']}]|[/] [{COLORS['text_dim']}]{short}[/] "
        f"[{COLORS['text_dim']}]|[/] [{COLORS['text_dim']}]{provider}[/]\n"
        f"[{COLORS['text_muted']}]Type a message or / for commands. Ctrl+D to exit.[/]\n"
    )


def _print_help() -> None:
    c = COLORS
    console.print(f"\n[bold {c['accent']}]Commands[/]")
    cmds = [
        ("/model <name>",   "Switch model"),
        ("/provider <name>","Switch provider (anthropic/openai/ollama)"),
        ("/theme <name>",   "Switch theme"),
        ("/skill <sub>",    "Manage skills (list/install/export/remove)"),
        ("/cost",           "Token usage and cost breakdown"),
        ("/status",         "Auth, model, provider info"),
        ("/copy",           "Copy last response to clipboard"),
        ("/export [file]",  "Export conversation to markdown"),
        ("/compact",        "Summarize conversation history"),
        ("/clear",          "Clear screen"),
        ("/exit",           "Quit"),
    ]
    for cmd, desc in cmds:
        console.print(f"  [{c['accent']}]{cmd:<22}[/] [{c['text_dim']}]{desc}[/]")
    console.print()


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        if platform.system() == "Darwin":
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(text.encode())
            return proc.returncode == 0
        elif platform.system() == "Linux":
            for cmd in ("xclip -selection clipboard", "xsel --clipboard --input"):
                try:
                    proc = subprocess.Popen(cmd.split(), stdin=subprocess.PIPE)
                    proc.communicate(text.encode())
                    if proc.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
        return False
    except Exception:
        return False


async def run_repl() -> None:
    """Main REPL loop."""
    from opentf.auth.credentials import CredentialManager
    from opentf.core.config import load_config

    config = load_config()
    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "anthropic")
    model = llm_cfg.get("model", get_default_model(provider))

    # Theme from config
    theme_name = config.get("theme", "gruvbox")
    set_theme(theme_name)

    creds = CredentialManager()

    # Check API key (skip for ollama)
    if provider != "ollama":
        key = creds.resolve_api_key(provider)
        if not key:
            env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
            console.print(
                f"\n[{COLORS['warning']}]No API key found for {provider}.[/]\n"
                f"Set [{COLORS['accent']}]{env_var}[/] or run with [{COLORS['accent']}]--tui[/] for interactive setup.\n"
            )
            # Prompt inline
            try:
                key = console.input(f"[{COLORS['text_dim']}]Paste your {provider} API key: [/]").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not key:
                return
            creds.store_api_key(key, provider=provider)

    engine = Engine(
        model=model,
        provider_name=provider,
    )
    cost_tracker = CostTracker()
    last_response = ""
    cancelled = False

    def _on_sigint(sig: int, frame: Any) -> None:
        nonlocal cancelled
        cancelled = True

    _print_banner(provider, model)

    try:
        while True:
            cancelled = False

            # Prompt
            try:
                user_input = console.input(f"[bold {COLORS['accent']}]>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print(f"\n[{COLORS['text_muted']}]Goodbye.[/]")
                break

            if not user_input:
                continue

            # --- Commands ---
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "/exit" or cmd == "/quit":
                    console.print(f"[{COLORS['text_muted']}]Goodbye.[/]")
                    break

                elif cmd == "/help":
                    _print_help()

                elif cmd == "/clear":
                    console.clear()

                elif cmd == "/model":
                    if not arg:
                        models = get_models(engine.llm.provider_name)
                        current = get_model_short_name(engine.llm.model)
                        console.print(f"[{COLORS['text_dim']}]Current: [bold]{current}[/][/]")
                        for short, mid in models.items():
                            marker = " [active]" if mid == engine.llm.model else ""
                            console.print(f"  [{COLORS['accent']}]{short}[/]{marker}")
                    else:
                        mid = resolve_model(engine.llm.provider_name, arg)
                        if mid:
                            engine.llm.model = mid
                            console.print(
                                f"[{COLORS['success']}]Switched to {get_model_short_name(mid)}[/]"
                            )
                        else:
                            console.print(f"[{COLORS['error']}]Unknown model: {arg}[/]")

                elif cmd == "/provider":
                    if not arg:
                        console.print(
                            f"[{COLORS['text_dim']}]Current: [bold]{engine.llm.provider_name}[/][/]\n"
                            f"  Available: anthropic, openai, ollama"
                        )
                    elif arg in ("anthropic", "openai", "ollama"):
                        engine.llm.provider_name = arg
                        engine.llm.model = get_default_model(arg)
                        engine.llm.reset_client()
                        console.print(
                            f"[{COLORS['success']}]Switched to {arg} "
                            f"({get_model_short_name(engine.llm.model)})[/]"
                        )
                    else:
                        console.print(f"[{COLORS['error']}]Unknown provider: {arg}[/]")

                elif cmd == "/theme":
                    if not arg:
                        themes = available_themes()
                        current = get_theme()
                        console.print(f"[{COLORS['text_dim']}]Current: [bold]{current}[/][/]")
                        for t in themes:
                            marker = " [active]" if t == current else ""
                            console.print(f"  [{COLORS['accent']}]{t}[/]{marker}")
                    else:
                        if set_theme(arg):
                            console.print(f"[{COLORS['success']}]Theme set to {arg}[/]")
                        else:
                            console.print(
                                f"[{COLORS['error']}]Unknown theme: {arg}. "
                                f"Available: {', '.join(available_themes())}[/]"
                            )

                elif cmd == "/copy":
                    if last_response:
                        if _copy_to_clipboard(last_response):
                            console.print(f"[{COLORS['success']}]Copied to clipboard.[/]")
                        else:
                            console.print(f"[{COLORS['error']}]Clipboard not available.[/]")
                    else:
                        console.print(f"[{COLORS['text_dim']}]Nothing to copy.[/]")

                elif cmd == "/export":
                    filename = arg or "conversation.md"
                    lines = []
                    for msg in engine.conversation_history:
                        role = msg.get("role", "unknown")
                        content = str(msg.get("content", ""))
                        if role == "user":
                            lines.append(f"## User\n\n{content}\n")
                        else:
                            lines.append(f"## Assistant\n\n{content}\n")
                    if lines:
                        Path(filename).write_text("\n".join(lines))
                        console.print(f"[{COLORS['success']}]Exported to {filename}[/]")
                    else:
                        console.print(f"[{COLORS['text_dim']}]No conversation to export.[/]")

                elif cmd == "/cost":
                    u = engine.llm.usage
                    short = get_model_short_name(engine.llm.model)
                    pricing = get_model_pricing(engine.llm.model)
                    inp_cost = u.input_tokens * pricing["input"] / 1_000_000
                    out_cost = u.output_tokens * pricing["output"] / 1_000_000
                    total = inp_cost + out_cost
                    rate = cost_tracker.dollars_per_hour(total)
                    console.print(
                        f"\n[bold]Cost[/] [{COLORS['text_dim']}]({short})[/]\n"
                        f"  input   {u.input_tokens:>8,} tokens  ${inp_cost:.4f}\n"
                        f"  output  {u.output_tokens:>8,} tokens  ${out_cost:.4f}\n"
                        f"  {'─' * 36}\n"
                        f"  [bold]total[/]                    [bold]${total:.4f}[/]"
                    )
                    if rate > 0:
                        console.print(f"  rate                     ${rate:.2f}/hr")
                    recent = cost_tracker.task_history[-5:]
                    if recent:
                        console.print(f"\n[bold]Recent[/]")
                        for t in recent:
                            console.print(
                                f"  ${t.cost:.4f}  [{COLORS['text_dim']}]{t.label}[/]"
                            )
                    console.print()

                elif cmd == "/status":
                    prov = engine.llm.provider_name
                    source = creds.resolve_source(prov)
                    key = creds.resolve_api_key(prov)
                    redacted = creds.redact_key(key) if key else "(none)"
                    console.print(
                        f"\n[bold]Status[/]\n"
                        f"  provider  [{COLORS['text_dim']}]{prov}[/]\n"
                        f"  auth      [{COLORS['text_dim']}]{source}[/]\n"
                        f"  key       [{COLORS['text_dim']}]{redacted}[/]\n"
                        f"  model     [{COLORS['text_dim']}]{engine.llm.model}[/]\n"
                        f"  tokens    [{COLORS['text_dim']}]{engine.llm.usage.total:,}[/]\n"
                        f"  theme     [{COLORS['text_dim']}]{get_theme()}[/]\n"
                    )

                elif cmd == "/skill":
                    sub_parts = user_input.split(maxsplit=2)
                    subcmd = sub_parts[1] if len(sub_parts) > 1 else "list"
                    subarg = sub_parts[2] if len(sub_parts) > 2 else ""
                    from opentf.core.skill_manager import SkillManager
                    mgr = SkillManager()
                    if subcmd == "list":
                        skills = mgr.list_skills()
                        if not skills:
                            console.print(f"[{COLORS['text_dim']}]No skills installed.[/]")
                        else:
                            console.print(f"\n[bold]Installed Skills[/]")
                            for s in skills:
                                console.print(
                                    f"  [{COLORS['accent']}]{s['name']}[/]  "
                                    f"v{s['version']}  "
                                    f"[{COLORS['text_dim']}]{s['description'][:50]}[/]"
                                )
                            console.print()
                    elif subcmd == "install" and subarg:
                        ok, msg = await mgr.install(subarg)
                        color = COLORS['success'] if ok else COLORS['error']
                        console.print(f"[{color}]{msg}[/]")
                    elif subcmd == "export" and subarg:
                        yaml_text = mgr.export_skill(subarg)
                        if yaml_text:
                            console.print(f"```yaml\n{yaml_text}```")
                        else:
                            console.print(f"[{COLORS['error']}]Skill not found: {subarg}[/]")
                    elif subcmd == "remove" and subarg:
                        if mgr.remove_skill(subarg):
                            console.print(f"[{COLORS['success']}]Removed {subarg}[/]")
                        else:
                            console.print(f"[{COLORS['error']}]Skill not found: {subarg}[/]")
                    else:
                        console.print(
                            f"Usage: /skill list | install <path> | export <name> | remove <name>"
                        )

                elif cmd == "/compact":
                    if len(engine.conversation_history) <= 5:
                        console.print(f"[{COLORS['text_dim']}]Nothing to compact.[/]")
                    else:
                        try:
                            from opentf.core.compaction import compact_history
                            before = len(engine.conversation_history)
                            compacted = await compact_history(
                                engine.llm, engine.conversation_history,
                            )
                            engine.conversation_history[:] = compacted
                            after = len(engine.conversation_history)
                            console.print(
                                f"[{COLORS['success']}]Compacted {before} -> {after} messages[/]"
                            )
                        except Exception as exc:
                            console.print(f"[{COLORS['error']}]Error: {exc}[/]")

                else:
                    console.print(
                        f"[{COLORS['text_dim']}]Unknown command: {cmd}. Type /help for commands.[/]"
                    )
                continue

            # --- Run agent ---
            console.print(f"[{COLORS['text_muted']}]{SPINNERS['dots'][0]} thinking...[/]", end="\r")

            # Set up approval handler
            async def _handle_approval(msg: Message) -> None:
                if msg.type != MessageType.APPROVAL_REQUESTED:
                    return
                cmd_text = msg.payload.get("command", "?")
                diff_text = msg.payload.get("diff_text")
                tool_id = msg.payload.get("tool_id", "")

                console.print(f"\n[bold {COLORS['warning']}]Approval needed:[/] {cmd_text}")
                if diff_text:
                    for line in diff_text.split("\n")[:30]:
                        if line.startswith("+"):
                            console.print(f"[{COLORS['success']}]{line}[/]")
                        elif line.startswith("-"):
                            console.print(f"[{COLORS['error']}]{line}[/]")
                        else:
                            console.print(f"[{COLORS['text_dim']}]{line}[/]")

                try:
                    choice = console.input(
                        f"[{COLORS['success']}]y[/]es / [{COLORS['error']}]n[/]o / "
                        f"[{COLORS['accent']}]a[/]lways: "
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "n"

                if choice in ("y", "yes"):
                    await engine.bus.publish(Message(
                        type=MessageType.APPROVAL_GRANTED, source="repl",
                        payload={"tool_id": tool_id},
                    ))
                elif choice in ("a", "always"):
                    await engine.bus.publish(Message(
                        type=MessageType.APPROVAL_ALWAYS, source="repl",
                        payload={"tool_id": tool_id},
                    ))
                else:
                    await engine.bus.publish(Message(
                        type=MessageType.APPROVAL_DENIED, source="repl",
                        payload={"tool_id": tool_id},
                    ))

            engine.bus.subscribe(MessageType.APPROVAL_REQUESTED, _handle_approval)

            cost_tracker.start_task(engine.llm.usage.total, 0.0)

            # Install signal handler for Ctrl+C during execution
            old_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _on_sigint)

            try:
                results = await engine.run(user_input)

                # Clear the "thinking..." line
                console.print(" " * 40, end="\r")

                for result in results:
                    if result["success"]:
                        response = result["output"].get("response", str(result["output"]))
                        if response:
                            last_response = response
                            console.print()
                            console.print(Markdown(response))
                            console.print()
                    else:
                        errors = result.get("errors", [])
                        for err in errors:
                            console.print(f"[{COLORS['error']}]{err}[/]")

                # Track cost
                u = engine.llm.usage
                pricing = get_model_pricing(engine.llm.model)
                total_cost = (
                    u.input_tokens * pricing["input"] / 1_000_000
                    + u.output_tokens * pricing["output"] / 1_000_000
                )
                cost_tracker.end_task(
                    user_input[:60], u.total, total_cost, engine.llm.model,
                )

            except Exception as exc:
                console.print(f"\n[{COLORS['error']}]Error: {exc}[/]")
            finally:
                signal.signal(signal.SIGINT, old_handler)
                engine.bus.unsubscribe(MessageType.APPROVAL_REQUESTED, _handle_approval)

    finally:
        await engine.close()


# Import here to avoid circular
from opentf.cli.theme import SPINNERS
