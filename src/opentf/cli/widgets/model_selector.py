"""Interactive model selector overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from opentf.cli.theme import COLORS

MODELS = [
    ("opus", "claude-opus-4-20250514", "Most capable, complex tasks"),
    ("sonnet", "claude-sonnet-4-20250514", "Balanced speed and quality"),
    ("haiku", "claude-haiku-4-5-20251001", "Fastest, lightweight tasks"),
]


class ModelSelected(Message):
    """Posted when user selects a model."""

    def __init__(self, model_id: str, short_name: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.short_name = short_name


class ModelSelector(Vertical):
    """Floating overlay for selecting an Anthropic model."""

    DEFAULT_CSS = f"""
    ModelSelector {{
        align: center middle;
        width: 100%;
        height: 100%;
        display: none;
        layer: overlay;
    }}
    ModelSelector #model-box {{
        width: 46;
        height: auto;
        max-height: 12;
        background: {COLORS['bg']};
        border: solid {COLORS['border']};
        padding: 1 1;
    }}
    ModelSelector #model-title {{
        text-align: center;
        color: {COLORS['text']};
        text-style: bold;
        margin-bottom: 1;
    }}
    ModelSelector OptionList {{
        height: auto;
        max-height: 6;
        background: {COLORS['bg']};
        color: {COLORS['text']};
        border: none;
        padding: 0;
    }}
    ModelSelector OptionList > .option-list--option-highlighted {{
        background: {COLORS['surface']};
        color: {COLORS['text']};
    }}
    ModelSelector #model-hint {{
        text-align: center;
        color: {COLORS['text_muted']};
        margin-top: 1;
    }}
    """

    can_focus = False

    def __init__(self, current_model: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_model = current_model

    def compose(self) -> ComposeResult:
        with Vertical(id="model-box"):
            yield Static("Switch Model", id="model-title")
            options = []
            for short, model_id, desc in MODELS:
                current = "  *" if model_id == self._current_model else ""
                label = f"  {short:<8} [{COLORS['text_dim']}]{desc}[/]{current}"
                options.append(Option(label, id=model_id))
            yield OptionList(*options, id="model-options")
            yield Static("[up/down] navigate  [enter] select  [esc] close", id="model-hint")

    def show(self, current_model: str) -> None:
        """Show the selector with the current model highlighted."""
        self._current_model = current_model
        self.display = True
        # Rebuild options to reflect current model
        try:
            opt_list = self.query_one("#model-options", OptionList)
            opt_list.clear_options()
            for short, model_id, desc in MODELS:
                current = "  *" if model_id == current_model else ""
                label = f"  {short:<8} [{COLORS['text_dim']}]{desc}[/]{current}"
                opt_list.add_option(Option(label, id=model_id))
            opt_list.focus()
        except Exception:
            pass

    def hide(self) -> None:
        """Hide the selector."""
        self.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User selected a model."""
        model_id = str(event.option_id)
        short = next((s for s, mid, _ in MODELS if mid == model_id), model_id)
        self.post_message(ModelSelected(model_id=model_id, short_name=short))
        self.hide()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.hide()
            event.prevent_default()
            event.stop()
