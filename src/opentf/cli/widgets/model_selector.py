"""Interactive model selector overlay with multi-provider support."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from opentf.cli.theme import COLORS, GLYPHS, MODEL_COLORS, gradient_text
from opentf.llm.registry import MODEL_REGISTRY

# Model descriptions for display
MODEL_DESCRIPTIONS: dict[str, str] = {
    # Anthropic
    "opus-4.6":   "Most capable, complex reasoning (4.6)",
    "sonnet-4.6": "Balanced speed and intelligence (4.6)",
    "sonnet":     "Previous gen balanced (4.0)",
    "haiku":      "Fastest, lightweight (4.5)",
    "haiku-3.5":  "Budget, fast responses (3.5)",
    # OpenAI
    "gpt-4.1":      "Latest GPT, strong coding",
    "gpt-4.1-mini": "Fast and affordable GPT",
    "gpt-4.1-nano": "Cheapest GPT, simple tasks",
    "gpt-4o":       "Multimodal GPT",
    "gpt-4o-mini":  "Fast multimodal GPT",
    "o3":           "Advanced reasoning",
    "o3-mini":      "Fast reasoning",
    "o4-mini":      "Latest reasoning, efficient",
    # Ollama
    "llama3.3":          "Meta Llama 3.3 (local)",
    "deepseek-coder-v2": "DeepSeek Coder v2 (local)",
    "codellama":         "Code-focused Llama (local)",
    "mistral":           "Mistral (local)",
    "qwen2.5-coder":     "Qwen 2.5 Coder (local)",
}

# Legacy compat: default models list for anthropic
MODELS = [
    ("opus-4.6", "claude-opus-4-6-20250515", "Most capable, complex reasoning (4.6)"),
    ("sonnet-4.6", "claude-sonnet-4-6-20250514", "Balanced speed and intelligence (4.6)"),
    ("sonnet", "claude-sonnet-4-20250514", "Previous gen balanced (4.0)"),
    ("haiku", "claude-haiku-4-5-20251001", "Fastest, lightweight (4.5)"),
    ("haiku-3.5", "claude-3-5-haiku-20241022", "Budget, fast responses (3.5)"),
]


def _get_models_for_provider(provider: str) -> list[tuple[str, str, str]]:
    """Get (short_name, model_id, description) tuples for a provider."""
    models = MODEL_REGISTRY.get(provider, {})
    result = []
    for short, model_id in models.items():
        desc = MODEL_DESCRIPTIONS.get(short, short)
        result.append((short, model_id, desc))
    return result


class ModelSelected(Message):
    """Posted when user selects a model."""

    def __init__(self, model_id: str, short_name: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.short_name = short_name


class ModelSelector(Vertical):
    """Floating overlay for selecting a model from the active provider."""

    DEFAULT_CSS = f"""
    ModelSelector {{
        align: center middle;
        width: 100%;
        height: 100%;
        display: none;
        layer: overlay;
    }}
    ModelSelector #model-box {{
        width: 52;
        height: auto;
        max-height: 16;
        background: {COLORS['bg']};
        border: round {COLORS['border']};
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
        max-height: 10;
        background: {COLORS['bg']};
        color: {COLORS['text']};
        border: none;
        padding: 0;
    }}
    ModelSelector OptionList > .option-list--option-highlighted {{
        background: {COLORS['surface']};
        color: {COLORS['text']};
        border-left: thick {COLORS['accent']};
    }}
    ModelSelector #model-hint {{
        text-align: center;
        color: {COLORS['text_muted']};
        margin-top: 1;
    }}
    """

    can_focus = False

    def __init__(self, current_model: str = "", provider: str = "anthropic", **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_model = current_model
        self._provider = provider

    def compose(self) -> ComposeResult:
        with Vertical(id="model-box"):
            title = gradient_text("Switch Model")
            yield Static(title, id="model-title")
            options = self._build_options(self._current_model)
            yield OptionList(*options, id="model-options")
            yield Static(
                f"[{COLORS['text_muted']}]up/down[/] navigate  "
                f"[{COLORS['text_muted']}]enter[/] select  "
                f"[{COLORS['text_muted']}]esc[/] close",
                id="model-hint",
            )

    def _build_options(self, current_model: str) -> list[Option]:
        """Build option list with colored pips and check marks."""
        models = _get_models_for_provider(self._provider)
        options: list[Option] = []
        for short, model_id, desc in models:
            color = MODEL_COLORS.get(short, COLORS['text'])
            pip = GLYPHS["bullet"]
            active = f" [{COLORS['success']}]{GLYPHS['check']}[/]" if model_id == current_model else ""
            label = (
                f"  [{color}]{pip}[/] "
                f"[bold {color}]{short:<12}[/] "
                f"[{COLORS['text_dim']}]{desc}[/]{active}"
            )
            options.append(Option(label, id=model_id))
        return options

    def show(self, current_model: str, provider: str | None = None) -> None:
        """Show the selector with the current model highlighted."""
        self._current_model = current_model
        if provider is not None:
            self._provider = provider
        self.display = True
        try:
            title = self.query_one("#model-title", Static)
            title.update(
                f"{gradient_text('Switch Model')} [{COLORS['text_dim']}]({self._provider})[/]"
            )
            opt_list = self.query_one("#model-options", OptionList)
            opt_list.clear_options()
            for option in self._build_options(current_model):
                opt_list.add_option(option)
            opt_list.focus()
        except Exception:
            pass

    def hide(self) -> None:
        """Hide the selector."""
        self.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User selected a model."""
        model_id = str(event.option_id)
        models = _get_models_for_provider(self._provider)
        short = next((s for s, mid, _ in models if mid == model_id), model_id)
        self.post_message(ModelSelected(model_id=model_id, short_name=short))
        self.hide()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.hide()
            event.prevent_default()
            event.stop()
