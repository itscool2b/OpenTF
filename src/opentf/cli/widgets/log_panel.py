"""Live log panel with styled icons and compact formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.widgets import RichLog

from opentf.cli.theme import COLORS, GLYPHS

# Glyph icons for different event types
STYLE_ICONS = {
    "tool":   GLYPHS["arrow_right"],
    "result": GLYPHS["check"],
    "error":  GLYPHS["cross"],
    "done":   GLYPHS["check"],
    "status": GLYPHS["bullet"],
}


class LogPanel(RichLog):
    """Scrollable live log of agent activity with styled icons."""

    DEFAULT_CSS = f"""
    LogPanel {{
        height: 6;
        background: {COLORS['panel']};
        border-top: solid {COLORS['border']};
        padding: 0 1;
        scrollbar-size: 1 1;
        display: none;
    }}
    """

    MAX_ENTRIES = 200

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, wrap=True, auto_scroll=True, **kwargs)
        self._entry_count = 0
        self._last_agent: str = ""

    def log_event(self, agent: str, action: str, style: str = "status") -> None:
        """Add a timestamped log entry with style-based icon."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        icon = STYLE_ICONS.get(style, GLYPHS["dot"])
        D = COLORS['text_dim']
        M = COLORS['text_muted']

        # Divider between different agents
        if self._last_agent and agent != self._last_agent:
            self.write(f"[{COLORS['border']}]{'─' * 40}[/]")
        self._last_agent = agent

        agent_display = f"[{COLORS['text']}]{agent:<8}[/]"

        if style == "tool":
            if "(" in action:
                tool_name, _, args = action.partition("(")
                action_display = f"[bold {COLORS['accent2']}]{tool_name}[/][{D}]({args}[/]"
            else:
                action_display = f"[bold {COLORS['accent2']}]{action}[/]"
            icon_display = f"[{COLORS['accent2']}]{icon}[/]"
        elif style == "result":
            action_display = f"[{D}]{action}[/]"
            icon_display = f"[{COLORS['success']}]{icon}[/]"
        elif style == "error":
            action_display = f"[{COLORS['error']}]{action}[/]"
            icon_display = f"[{COLORS['error']}]{icon}[/]"
        elif style == "done":
            action_display = f"[{COLORS['success']}]{action}[/]"
            icon_display = f"[{COLORS['success']}]{icon}[/]"
        else:
            action_display = f"[{D}]{action}[/]"
            icon_display = f"[{M}]{icon}[/]"

        line = f"[{M}]{now}[/] {agent_display} {icon_display} {action_display}"
        self.write(line)

        self._entry_count += 1
        if self._entry_count > self.MAX_ENTRIES:
            self.clear()
            self._entry_count = 0
