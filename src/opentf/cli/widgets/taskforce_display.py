"""Dedicated task force ops-center display.

Split-panel interface showing animated agent cards on the left
and styled live tool activity on the right.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog, Static

from opentf.cli.theme import BORDERS, COLORS, GLYPHS, SPINNERS, gradient_text, get_border_style
from opentf.cli.renderables import animated_progress_bar, key_badge, status_badge

_A = COLORS['accent']
_B = COLORS['border']
_D = COLORS['text_dim']
_S = COLORS['success']
_E = COLORS['error']
_M = COLORS['text_muted']


class TaskForceHeader(Static):
    """Top bar with gradient title, shimmer progress bar, and elapsed time."""

    DEFAULT_CSS = f"""
    TaskForceHeader {{
        height: 1;
        background: {COLORS['surface']};
        color: {COLORS['text']};
        padding: 0 2;
    }}
    """

    def __init__(self, label: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._done = 0
        self._total = 0
        self._start = time.monotonic()
        self._frame = 0
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0, self._tick)
        self._refresh()

    def _tick(self) -> None:
        self._frame += 1
        self._refresh()

    def set_progress(self, done: int, total: int) -> None:
        self._done = done
        self._total = total
        self._refresh()

    def _refresh(self) -> None:
        elapsed = time.monotonic() - self._start
        if elapsed < 60:
            time_str = f"{elapsed:.0f}s"
        else:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            time_str = f"{mins}m{secs:02d}s"

        title = gradient_text("TASK FORCE")
        bar = ""
        if self._total > 0:
            bar = f"  {animated_progress_bar(self._done, self._total, 20, self._frame)}  "

        done_display = f"[{_S}]{self._done}[/]" if self._done > 0 else f"[{_D}]0[/]"
        elapsed_badge = f"[{_M}]{GLYPHS['bullet']} {time_str}[/]"

        self.update(
            f"[bold]{title}[/]  [{_D}]{self._label}[/]"
            f"    {bar}"
            f"{done_display}[{_D}]/{self._total}[/]"
            f"  {elapsed_badge}"
        )

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None


class AgentEntry(Static):
    """Single agent displayed as a box-drawn card with animated spinner."""

    DEFAULT_CSS = f"""
    AgentEntry {{
        height: auto;
        padding: 0 1;
        margin-bottom: 0;
    }}
    """

    def __init__(self, name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent_name = name
        self._status = "waiting"
        self._action = "queued"
        self._frame = 0
        self._anim_timer = None
        self._refresh()

    def set_active(self, action: str = "working...") -> None:
        self._status = "active"
        self._action = action
        if not self._anim_timer:
            self._anim_timer = self.set_interval(0.15, self._tick_anim)
        self._refresh()

    def set_done(self, action: str = "complete") -> None:
        self._status = "done"
        self._action = action
        self._stop_anim()
        self._refresh()

    def set_failed(self, action: str = "failed") -> None:
        self._status = "failed"
        self._action = action
        self._stop_anim()
        self._refresh()

    def _stop_anim(self) -> None:
        if self._anim_timer:
            self._anim_timer.stop()
            self._anim_timer = None

    def _tick_anim(self) -> None:
        self._frame += 1
        self._refresh()

    def _refresh(self) -> None:
        b = BORDERS[get_border_style()]
        width = 38
        inner = width - 2

        status_colors = {
            "active": _A, "done": _S, "failed": _E, "waiting": _M,
        }
        status_labels = {
            "active": "ACTIVE", "done": "DONE", "failed": "FAILED", "waiting": "WAIT",
        }
        spinners = {
            "active": SPINNERS["pulse"][self._frame % len(SPINNERS["pulse"])],
            "done":   GLYPHS["check"],
            "failed": GLYPHS["cross"],
            "waiting": GLYPHS["dot"],
        }

        c = status_colors.get(self._status, _M)
        label = status_labels.get(self._status, "?")
        spinner = spinners.get(self._status, GLYPHS["dot"])

        # Top line: ╭─ name ──────── BADGE ╮
        badge = f"[bold {c}]{label}[/]"
        name_part = f" {self.agent_name} "
        badge_raw = f" {label} "
        pad = max(inner - len(name_part) - len(badge_raw) - 1, 0)
        top = (
            f"[{c}]{b['tl']}{b['h']}[/]"
            f"[bold {COLORS['text']}]{name_part}[/]"
            f"[{c}]{b['h'] * pad}[/]"
            f" {badge}"
            f"[{c}]{b['tr']}[/]"
        )

        # Middle: │  ◐ action text           │
        action_text = f" {spinner} {self._action}"
        action_pad = max(inner - len(action_text) - 1, 0)
        mid = (
            f"[{c}]{b['v']}[/]"
            f"[{_D}]{action_text}{' ' * action_pad}[/]"
            f"[{c}]{b['v']}[/]"
        )

        # Bottom: ╰───────────────────────────╯
        bottom = f"[{c}]{b['bl']}{b['h'] * inner}{b['br']}[/]"

        self.update(f"{top}\n{mid}\n{bottom}")


class AgentPanel(Vertical):
    """Left panel showing all agents as styled cards."""

    DEFAULT_CSS = f"""
    AgentPanel {{
        width: 1fr;
        border-right: solid {COLORS['border']};
        padding: 0 0;
        overflow-y: auto;
    }}
    AgentPanel #tf-agents-title {{
        height: 1;
        background: {COLORS['panel']};
        padding: 0 2;
        color: {COLORS['accent']};
        text-style: bold;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: dict[str, AgentEntry] = {}

    def compose(self) -> ComposeResult:
        yield Static(
            f"{GLYPHS['agent']} {gradient_text('Agents')}",
            id="tf-agents-title",
        )

    def add_agent(self, name: str) -> None:
        if name not in self._entries:
            entry = AgentEntry(name, id=f"tf-agent-{name}")
            self._entries[name] = entry
            self.mount(entry)

    def set_active(self, name: str, action: str = "working...") -> None:
        if name in self._entries:
            self._entries[name].set_active(action)

    def set_done(self, name: str) -> None:
        if name in self._entries:
            self._entries[name].set_done()

    def set_failed(self, name: str, error: str = "failed") -> None:
        if name in self._entries:
            self._entries[name].set_failed(error)


class LivePanel(RichLog):
    """Right panel showing live tool activity with colored left-gutter."""

    DEFAULT_CSS = f"""
    LivePanel {{
        width: 1fr;
        padding: 0 1;
        background: {COLORS['bg']};
    }}
    LivePanel #tf-live-title {{
        height: 1;
        background: {COLORS['panel']};
        padding: 0 2;
        color: {COLORS['accent']};
        text-style: bold;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, wrap=True, auto_scroll=True, **kwargs)
        self._last_agent: str = ""

    def log_tool(self, agent: str, tool: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        # Divider between different agents
        if self._last_agent and agent != self._last_agent:
            self.write(f"[{_B}]{'─' * 44}[/]")
        self._last_agent = agent

        gutter = f"[{COLORS['accent2']}]{GLYPHS['arrow_right']}[/]"
        if detail:
            self.write(
                f" {gutter} [{_M}]{now}[/] [{_A}]{agent}[/] "
                f"[bold {COLORS['accent2']}]{tool}[/] [{_D}]{detail[:55]}[/]"
            )
        else:
            self.write(
                f" {gutter} [{_M}]{now}[/] [{_A}]{agent}[/] "
                f"[bold {COLORS['accent2']}]{tool}[/]"
            )

    def log_result(self, agent: str, tool: str, ok: bool, summary: str = "") -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if ok:
            gutter = f"[{_S}]{GLYPHS['check']}[/]"
        else:
            gutter = f"[{_E}]{GLYPHS['cross']}[/]"
        self.write(
            f" {gutter} [{_M}]{now}[/] [{_A}]{agent}[/] [{_D}]{summary[:55]}[/]"
        )

    def log_status(self, agent: str, status: str) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        gutter = f"[{_M}]{GLYPHS['bullet']}[/]"
        self.write(f" {gutter} [{_M}]{now}[/] [{_A}]{agent}[/] [{_D}]{status}[/]")


class TaskForceFooter(Static):
    """Bottom bar with key badges and live summary."""

    DEFAULT_CSS = f"""
    TaskForceFooter {{
        height: 1;
        background: {COLORS['surface']};
        color: {COLORS['text_muted']};
        padding: 0 2;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._wave = 0
        self._total_waves = 0
        self._active_count = 0
        self._tool_count = 0
        self._refresh()

    def _refresh(self) -> None:
        keys = (
            f"{key_badge('Esc', 'cancel')}   "
            f"{key_badge('Ctrl+C', 'abort')}"
        )
        summary_parts: list[str] = []
        if self._total_waves > 0:
            summary_parts.append(f"Wave {self._wave}/{self._total_waves}")
        if self._active_count > 0:
            summary_parts.append(f"{self._active_count} active")
        if self._tool_count > 0:
            summary_parts.append(f"{self._tool_count} tools")
        summary = f" [{_M}]{GLYPHS['sep']}[/] ".join(
            f"[{_D}]{p}[/]" for p in summary_parts
        )
        sep = f"    [{_M}]{GLYPHS['sep']}[/]    " if summary else ""
        self.update(f"  {keys}{sep}{summary}")

    def update_summary(
        self,
        wave: int = 0,
        total_waves: int = 0,
        active: int = 0,
        tools: int = 0,
    ) -> None:
        self._wave = wave
        self._total_waves = total_waves
        self._active_count = active
        self._tool_count = tools
        self._refresh()


class TaskForceDisplay(Vertical):
    """Full task force ops-center interface with agent cards and live feed."""

    DEFAULT_CSS = f"""
    TaskForceDisplay {{
        height: 1fr;
        background: {COLORS['bg']};
    }}
    """

    def __init__(self, label: str = "", agents: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._agent_names = agents or []

    def compose(self) -> ComposeResult:
        yield TaskForceHeader(label=self._label, id="tf-header")
        with Horizontal(id="tf-panels"):
            panel = AgentPanel(id="tf-agents")
            yield panel
            yield LivePanel(id="tf-live")
        yield TaskForceFooter(id="tf-footer")

    def on_mount(self) -> None:
        panel = self.query_one(AgentPanel)
        for name in self._agent_names:
            panel.add_agent(name)

    @property
    def header(self) -> TaskForceHeader:
        return self.query_one(TaskForceHeader)

    @property
    def agents(self) -> AgentPanel:
        return self.query_one(AgentPanel)

    @property
    def live(self) -> LivePanel:
        return self.query_one(LivePanel)

    @property
    def footer(self) -> TaskForceFooter:
        return self.query_one(TaskForceFooter)
