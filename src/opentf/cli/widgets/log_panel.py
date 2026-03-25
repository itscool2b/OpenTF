"""Live log panel with tool-type icons and compact formatting."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.widgets import RichLog

from opentf.cli.theme import COLORS

# Style icons for different event types
STYLE_ICONS = {
    "tool":   "\u26a1",   # lightning
    "result": "\u2713",   # checkmark
    "error":  "\u2717",   # x mark
    "done":   "\u2713",   # checkmark
    "status": "\u2022",   # bullet
}


class LogPanel(RichLog):
    """Scrollable live log of agent activity with icons."""

    DEFAULT_CSS = f"""
    LogPanel {{
        height: 6;
        background: {COLORS['panel']};
        border-top: solid {COLORS['border']};
        padding: 0 1;
        scrollbar-size: 1 1;
    }}
    """

    MAX_ENTRIES = 200

    def __init__(self, **kwargs) -> None:
        super().__init__(markup=True, wrap=True, auto_scroll=True, **kwargs)
        self._entry_count = 0

    def log_event(self, agent: str, action: str, style: str = "status") -> None:
        """Add a timestamped log entry with style-based icon."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        icon = STYLE_ICONS.get(style, "\u2022")

        agent_display = f"[{COLORS['text']}]{agent:<10}[/]"

        if style == "tool":
            action_display = f"[{COLORS['accent2']}]{action}[/]"
            icon_display = f"[{COLORS['accent2']}]{icon}[/]"
        elif style == "result":
            action_display = f"[{COLORS['text_dim']}]{action}[/]"
            icon_display = f"[{COLORS['success']}]{icon}[/]"
        elif style == "error":
            action_display = f"[{COLORS['error']}]{action}[/]"
            icon_display = f"[{COLORS['error']}]{icon}[/]"
        elif style == "done":
            action_display = f"[{COLORS['success']}]{action}[/]"
            icon_display = f"[{COLORS['success']}]{icon}[/]"
        else:
            action_display = f"[{COLORS['text_dim']}]{action}[/]"
            icon_display = f"[{COLORS['text_muted']}]{icon}[/]"

        line = f"[{COLORS['text_muted']}]{now}[/]  {agent_display} {icon_display} {action_display}"
        self.write(line)

        self._entry_count += 1
        if self._entry_count > self.MAX_ENTRIES:
            self.clear()
            self._entry_count = 0
