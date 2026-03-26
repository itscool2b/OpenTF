"""Input prompt with history, mode-aware styling, and mode indicator pip."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import Input

from opentf.cli.theme import COLORS, GLYPHS


class SlashToggle(Message):
    """Posted when slash state changes (show/hide palette)."""

    def __init__(self, active: bool) -> None:
        super().__init__()
        self.active = active


class PromptInput(Input):
    """Single-line input with command history, mode badges, and indicator pip."""

    DEFAULT_CSS = f"""
    PromptInput {{
        height: 3;
        background: {COLORS['bg']};
        border-top: tall {COLORS['border']};
        border-bottom: none;
        border-left: none;
        border-right: none;
        padding: 0 1;
        color: {COLORS['text']};
    }}
    PromptInput:focus {{
        border-top: tall {COLORS['border_focus']};
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(placeholder="Type a message, or / for commands", **kwargs)
        self._history: list[str] = []
        self._history_index = -1
        self._mode = "normal"

    def add_to_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_index = -1

    def _on_key(self, event: events.Key) -> None:
        # When typing a slash command and pressing Down, focus the palette
        if event.key == "down" and self.value.startswith("/"):
            try:
                from opentf.cli.widgets.command_palette import CommandPalette
                palette = self.app.query_one(CommandPalette)
                if palette.display:
                    palette.focus_list()
                    event.prevent_default()
                    return
            except Exception:
                pass

        if event.key == "up" and self._history:
            if self._history_index == -1:
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            self.value = self._history[self._history_index]
            event.prevent_default()
        elif event.key == "down":
            if self._history_index >= 0:
                self._history_index += 1
                if self._history_index >= len(self._history):
                    self._history_index = -1
                    self.value = ""
                else:
                    self.value = self._history[self._history_index]
            event.prevent_default()

    def set_mode(self, mode: str) -> None:
        """Switch prompt placeholder and pip color for different modes."""
        self._mode = mode
        mode_config = {
            "plan": {
                "placeholder": "Describe your goal, or /confirm /cancel",
                "pip_color": COLORS["warning"],
            },
            "janitor": {
                "placeholder": "accept N, reject N, /done, or /cancel",
                "pip_color": COLORS["accent2"],
            },
        }
        cfg = mode_config.get(mode, {})
        self.placeholder = cfg.get(
            "placeholder", "Type a message, or / for commands"
        )

    def watch_value(self, value: str) -> None:
        """Show/hide palette reactively based on whether input starts with /."""
        if value.startswith("/"):
            self.post_message(SlashToggle(active=True))
        else:
            self.post_message(SlashToggle(active=False))
