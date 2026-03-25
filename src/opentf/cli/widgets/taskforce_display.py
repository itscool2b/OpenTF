"""Dedicated task force ops-center display.

Split-panel interface showing agent status on the left
and live tool activity on the right.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import RichLog, Static

from opentf.cli.theme import COLORS

_A = COLORS['accent']
_B = COLORS['border']
_D = COLORS['text_dim']
_S = COLORS['success']
_E = COLORS['error']
_M = COLORS['text_muted']


class TaskForceHeader(Static):
    """Top bar with mission label, progress, and elapsed time."""

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
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(1.0, self._tick)
        self._refresh()

    def _tick(self) -> None:
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

        bar = ""
        if self._total > 0:
            filled = int((self._done / self._total) * 20)
            bar = f" [{_S}]{'=' * filled}[/][{_M}]{'.' * (20 - filled)}[/] "

        self.update(
            f"[bold {_A}]TASK FORCE[/]  [{_D}]{self._label}[/]"
            f"    {bar}"
            f"[{_D}]{self._done}/{self._total}[/]"
            f"  [{_M}]{time_str}[/]"
        )

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()
            self._timer = None


class AgentEntry(Static):
    """Single agent status line in the agent panel."""

    DEFAULT_CSS = f"""
    AgentEntry {{
        height: 2;
        padding: 0 1;
    }}
    """

    def __init__(self, name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.agent_name = name
        self._status = "waiting"
        self._action = "queued"
        self._refresh()

    def set_active(self, action: str = "working...") -> None:
        self._status = "active"
        self._action = action
        self._refresh()

    def set_done(self, action: str = "complete") -> None:
        self._status = "done"
        self._action = action
        self._refresh()

    def set_failed(self, action: str = "failed") -> None:
        self._status = "failed"
        self._action = action
        self._refresh()

    def _refresh(self) -> None:
        icons = {
            "active": f"[{_A}]>>[/]",
            "done": f"[{_S}]ok[/]",
            "failed": f"[{_E}]!![/]",
            "waiting": f"[{_M}]--[/]",
        }
        colors = {
            "active": _A,
            "done": _S,
            "failed": _E,
            "waiting": _M,
        }
        icon = icons.get(self._status, f"[{_M}]--[/]")
        color = colors.get(self._status, _M)
        self.update(
            f" {icon} [bold {color}]{self.agent_name}[/]\n"
            f"      [{_D}]{self._action}[/]"
        )


class AgentPanel(Vertical):
    """Left panel showing all agents and their status."""

    DEFAULT_CSS = f"""
    AgentPanel {{
        width: 1fr;
        border-right: solid {COLORS['border']};
        padding: 1 0;
        overflow-y: auto;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: dict[str, AgentEntry] = {}

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
    """Right panel showing live tool activity from agents."""

    DEFAULT_CSS = f"""
    LivePanel {{
        width: 1fr;
        padding: 0 1;
        background: {COLORS['bg']};
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, wrap=True, auto_scroll=True, **kwargs)

    def log_tool(self, agent: str, tool: str, detail: str = "") -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if detail:
            self.write(
                f"[{_M}]{now}[/] [{_A}]{agent}[/] [{COLORS['accent2']}]{tool}[/] [{_D}]{detail[:60]}[/]"
            )
        else:
            self.write(
                f"[{_M}]{now}[/] [{_A}]{agent}[/] [{COLORS['accent2']}]{tool}[/]"
            )

    def log_result(self, agent: str, tool: str, ok: bool, summary: str = "") -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        color = _S if ok else _E
        icon = "ok" if ok else "!!"
        self.write(
            f"[{_M}]{now}[/] [{_A}]{agent}[/] [{color}]{icon}[/] [{_D}]{summary[:60]}[/]"
        )

    def log_status(self, agent: str, status: str) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.write(f"[{_M}]{now}[/] [{_A}]{agent}[/] [{_D}]{status}[/]")


class TaskForceFooter(Static):
    """Bottom bar with controls hint."""

    DEFAULT_CSS = f"""
    TaskForceFooter {{
        height: 1;
        background: {COLORS['surface']};
        color: {COLORS['text_muted']};
        padding: 0 2;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            f"  [{_D}]Esc[/] cancel",
            **kwargs,
        )


class TaskForceDisplay(Vertical):
    """Full task force ops-center interface."""

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
