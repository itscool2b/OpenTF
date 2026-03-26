"""Dark, minimal command palette with search filtering and category icons."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from opentf.cli.theme import COLORS, GLYPHS, gradient_text

# Category icons for different command types
_CAT_AGENT = GLYPHS["agent"]
_CAT_NAV = GLYPHS["arrow_right"]
_CAT_INFO = GLYPHS["diamond"]
_CAT_FILE = GLYPHS["dot"]

COMMANDS = [
    ("/taskforce", "Spawn parallel agent swarm for big projects", _CAT_AGENT),
    ("/plan", "Enter interactive planning mode", _CAT_AGENT),
    ("/janitor", "Scan for code quality issues", _CAT_AGENT),
    ("/model", "Switch model", _CAT_NAV),
    ("/resume", "Resume previous session", _CAT_NAV),
    ("/new", "Start a new session", _CAT_NAV),
    ("/save", "Save session with a name", _CAT_FILE),
    ("/sessions", "List saved sessions", _CAT_INFO),
    ("/undo", "Undo last file change", _CAT_FILE),
    ("/review", "Toggle manual review for file changes", _CAT_FILE),
    ("/compact", "Summarize conversation history", _CAT_NAV),
    ("/help", "Show available commands", _CAT_INFO),
    ("/status", "Auth, model, token usage", _CAT_INFO),
    ("/cost", "Token usage and estimated cost", _CAT_INFO),
    ("/login", "Set or change API key", _CAT_NAV),
    ("/logout", "Clear stored credentials", _CAT_NAV),
    ("/provider", "Switch LLM provider (anthropic/openai/ollama)", _CAT_NAV),
    ("/skill", "Manage skills (list/install/export/remove)", _CAT_AGENT),
    ("/theme", "Switch color theme", _CAT_NAV),
    ("/copy", "Copy last response to clipboard", _CAT_FILE),
    ("/export", "Export conversation to markdown file", _CAT_FILE),
    ("/clear", "Clear the output", _CAT_NAV),
    ("/exit", "Quit OpenTF", _CAT_NAV),
]


class CommandSelected(Message):
    """Posted when user selects a command from the palette."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command


class CommandPalette(Vertical):
    """Dark, filterable command palette overlay with category icons."""

    DEFAULT_CSS = f"""
    CommandPalette {{
        height: auto;
        max-height: 16;
        background: {COLORS['bg']};
        border: solid {COLORS['border']};
        display: none;
        layer: overlay;
        dock: bottom;
        margin-bottom: 2;
        padding: 0 0;
    }}
    CommandPalette #palette-title {{
        height: 1;
        background: {COLORS['surface']};
        padding: 0 2;
        color: {COLORS['text']};
    }}
    CommandPalette OptionList {{
        height: auto;
        max-height: 13;
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
        title = gradient_text("Commands")
        yield Static(f"  {title}", id="palette-title")
        options = self._build_options("")
        yield OptionList(*options, id="cmd-options")

    def _build_options(self, query: str) -> list[Option]:
        """Build filtered option list with category icons."""
        options: list[Option] = []
        for item in self._all_commands:
            cmd, desc = item[0], item[1]
            icon = item[2] if len(item) > 2 else GLYPHS["dot"]
            if query and query not in cmd:
                continue
            label = (
                f"[{COLORS['text_muted']}]{icon}[/] "
                f"[bold {COLORS['text']}]{cmd:<14}[/] "
                f"[{COLORS['text_dim']}]{desc}[/]"
            )
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
