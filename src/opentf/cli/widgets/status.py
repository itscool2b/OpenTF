"""Header bar with branding, model badge, token count, cost, and elapsed time."""

from __future__ import annotations

from textual.widgets import Static

from opentf.cli.theme import COLORS, MODEL_COLORS, SONNET_INPUT_PRICE, SONNET_OUTPUT_PRICE


def _model_short(model: str) -> str:
    """Extract short name from model ID."""
    if "opus" in model:
        return "opus"
    if "haiku" in model:
        return "haiku"
    return "sonnet"


class HeaderBar(Static):
    """Top bar: brand + model badge + metrics."""

    DEFAULT_CSS = f"""
    HeaderBar {{
        height: 1;
        background: {COLORS['surface']};
        color: {COLORS['text']};
        padding: 0 1;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = "claude-sonnet-4-20250514"
        self._tokens = 0
        self._cost = 0.0
        self._elapsed = ""

    def on_mount(self) -> None:
        self._refresh()

    def update_tokens(self, tokens: int) -> None:
        self._tokens = tokens
        self._refresh()

    def update_model(self, model: str) -> None:
        self._model = model
        self._refresh()

    def update_elapsed(self, elapsed: str) -> None:
        self._elapsed = elapsed
        self._refresh()

    def update_cost(self, cost: float) -> None:
        self._cost = cost
        self._refresh()

    def _refresh(self) -> None:
        short = _model_short(self._model)
        badge_color = MODEL_COLORS.get(short, COLORS["text_dim"])

        left = f"[bold {COLORS['text']}]OpenTF[/]"

        parts = []
        # Model badge
        parts.append(f"[{badge_color}]{short}[/]")
        # Token count
        if self._tokens:
            if self._tokens >= 1000:
                parts.append(f"[{COLORS['text_dim']}]{self._tokens / 1000:.1f}k tokens[/]")
            else:
                parts.append(f"[{COLORS['text_dim']}]{self._tokens} tokens[/]")
        # Cost
        if self._cost > 0:
            parts.append(f"[{COLORS['text_dim']}]${self._cost:.4f}[/]")
        # Elapsed
        if self._elapsed:
            parts.append(f"[{COLORS['text_muted']}]{self._elapsed}[/]")

        right = f" [{COLORS['border']}]|[/] ".join(parts)
        self.update(f"{left}    {right}")
