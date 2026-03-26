"""Compact activity bar with animated spinners, badges, and tool count."""

from __future__ import annotations

from textual.widgets import Static

from opentf.cli.theme import COLORS, GLYPHS, SPINNERS

SPINNER_FRAMES = SPINNERS["pulse"]


class ActivityBar(Static):
    """Single-line bar showing current agent activity. Auto-hides when idle."""

    DEFAULT_CSS = f"""
    ActivityBar {{
        height: 2;
        background: {COLORS['panel']};
        color: {COLORS['text_dim']};
        padding: 0 1;
        border-top: solid {COLORS['border']};
        display: none;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(" ", **kwargs)
        self._agents: dict[str, dict] = {}
        self._frame: int = 0
        self._tool_count: int = 0
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        self._frame += 1
        if any(a["state"] == "active" for a in self._agents.values()):
            self._refresh_bar()

    def add_agent(self, name: str, action: str, state: str = "active") -> None:
        self._agents[name] = {"action": action, "state": state}
        if state == "active":
            self.display = True
        self._refresh_bar()

    def increment_tool_count(self) -> None:
        self._tool_count += 1
        self._refresh_bar()

    def complete_agent(self, name: str, message: str = "done") -> None:
        if name in self._agents:
            self._agents[name] = {"action": message, "state": "done"}
            self._refresh_bar()

    def fail_agent(self, name: str, message: str = "failed") -> None:
        if name in self._agents:
            self._agents[name] = {"action": message, "state": "error"}
            self._refresh_bar()

    def clear_agents(self) -> None:
        self._agents.clear()
        self._tool_count = 0
        self.display = False
        self.update(" ")

    def _refresh_bar(self) -> None:
        sep = f" [{COLORS['border']}]{GLYPHS['sep']}[/] "
        parts: list[str] = []
        for name, info in self._agents.items():
            state = info["state"]
            action = info["action"]
            if state == "active":
                frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
                parts.append(
                    f"[{COLORS['accent']}]{frame}[/] [bold]{name}[/] "
                    f"[{COLORS['text_dim']}]{action}[/]"
                )
            elif state == "done":
                parts.append(
                    f"[{COLORS['success']}]{GLYPHS['check']}[/] [bold]{name}[/] "
                    f"[{COLORS['text_dim']}]{action}[/]"
                )
            elif state == "error":
                parts.append(
                    f"[{COLORS['error']}]{GLYPHS['cross']}[/] [bold]{name}[/] "
                    f"[{COLORS['text_dim']}]{action}[/]"
                )
            else:
                parts.append(
                    f"[{COLORS['text_muted']}]{GLYPHS['dot']}[/] [bold]{name}[/] "
                    f"[{COLORS['text_dim']}]{action}[/]"
                )

        text = sep.join(parts) if parts else " "

        if self._tool_count > 0:
            text += (
                f" {sep}"
                f"[{COLORS['text_muted']}]{GLYPHS['diamond']} {self._tool_count} tools[/]"
            )

        self.update(text)
