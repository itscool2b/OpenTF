"""Dark, minimal command palette with search filtering."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from opentf.cli.theme import COLORS

COMMANDS = [
    ("/taskforce", "Spawn parallel agent swarm for big projects"),
    ("/plan", "Enter interactive planning mode"),
    ("/janitor", "Scan for code quality issues"),
    ("/model", "Switch model"),
    ("/resume", "Resume previous session"),
    ("/new", "Start a new session"),
    ("/save", "Save session with a name"),
    ("/sessions", "List saved sessions"),
    ("/undo", "Undo last file change"),
    ("/review", "Toggle manual review for file changes"),
    ("/compact", "Summarize conversation history"),
    ("/help", "Show available commands"),
    ("/status", "Auth, model, token usage"),
    ("/cost", "Token usage and estimated cost"),
    ("/login", "Set or change API key"),
    ("/logout", "Clear stored credentials"),
    ("/clear", "Clear the output"),
    ("/exit", "Quit OpenTF"),
]


class CommandSelected(Message):
    """Posted when user selects a command from the palette."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command


class CommandPalette(Vertical):
    """Dark, filterable command palette overlay."""

    DEFAULT_CSS = f"""
    CommandPalette {{
        height: auto;
        max-height: 14;
        background: {COLORS['bg']};
        border: solid {COLORS['border']};
        display: none;
        layer: overlay;
        dock: bottom;
        margin-bottom: 2;
        padding: 1 0;
    }}
    CommandPalette OptionList {{
        height: auto;
        max-height: 12;
        background: {COLORS['bg']};
        color: {COLORS['text']};
        border: none;
        padding: 0 1;
        scrollbar-size: 1 1;
    }}
    CommandPalette OptionList > .option-list--option-highlighted {{
        background: {COLORS['surface']};
        color: {COLORS['text']};
        border-left: thick {COLORS['accent']};
    }}
    """

    can_focus = False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._all_commands = COMMANDS

    def compose(self) -> ComposeResult:
        options = self._build_options("")
        yield OptionList(*options, id="cmd-options")

    def _build_options(self, query: str) -> list[Option]:
        """Build filtered option list."""
        options = []
        for cmd, desc in self._all_commands:
            if query and query not in cmd:
                continue
            label = f"[bold {COLORS['text']}]{cmd:<14}[/] [{COLORS['text_dim']}]{desc}[/]"
            options.append(Option(label, id=cmd))
        return options

    def filter(self, query: str) -> None:
        """Filter commands based on query text."""
        try:
            opt_list = self.query_one("#cmd-options", OptionList)
            opt_list.clear_options()
            for opt in self._build_options(query):
                opt_list.add_option(opt)
        except Exception:
            pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User selected a command."""
        command = str(event.option_id)
        self.post_message(CommandSelected(command=command))
        self.display = False

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.display = False
            event.prevent_default()
            event.stop()
