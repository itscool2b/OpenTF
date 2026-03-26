"""Header bar with branding, model badge, token count, cost, and elapsed time."""

from __future__ import annotations

import time

from textual.widgets import Static

from opentf.cli.theme import COLORS, GLYPHS, MODEL_COLORS, gradient_text
from opentf.llm.registry import get_model_short_name


def _model_short(model: str) -> str:
    """Extract short name from model ID."""
    return get_model_short_name(model)


class HeaderBar(Static):
    """Top bar: gradient brand + model badge + metrics with box-drawing seps."""

    DEFAULT_CSS = f"""
    HeaderBar {{
        height: 1;
        background: {COLORS['surface']};
        color: {COLORS['text']};
        padding: 0 2;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = "claude-sonnet-4-20250514"
        self._tokens = 0
        self._cost = 0.0
        self._rate = 0.0
        self._elapsed = ""
        self._timer_start: float = 0.0
        self._elapsed_timer = None
        self._pulse_on: bool = True

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

    def start_timer(self) -> None:
        """Begin live elapsed time updates."""
        self._timer_start = time.monotonic()
        self._elapsed = "0s"
        self._pulse_on = True
        self._refresh()
        self._elapsed_timer = self.set_interval(0.5, self._tick_elapsed)

    def stop_timer(self) -> None:
        """Stop live elapsed updates and freeze the display."""
        if self._elapsed_timer:
            self._elapsed_timer.stop()
            self._elapsed_timer = None

    def update_cost(self, cost: float) -> None:
        self._cost = cost
        self._refresh()

    def update_rate(self, rate: float) -> None:
        self._rate = rate
        self._refresh()

    def _tick_elapsed(self) -> None:
        """Update elapsed display and pulse indicator."""
        elapsed = time.monotonic() - self._timer_start
        if elapsed < 60:
            self._elapsed = f"{elapsed:.0f}s"
        else:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            self._elapsed = f"{mins}m{secs:02d}s"
        self._pulse_on = not self._pulse_on
        self._refresh()

    def _refresh(self) -> None:
        short = _model_short(self._model)
        badge_color = MODEL_COLORS.get(short, COLORS["text_dim"])
        sep = f" [{COLORS['text_muted']}]{GLYPHS['sep']}[/] "

        # Gradient brand
        left = gradient_text("OpenTF")

        parts: list[str] = []
        # Model badge with color
        parts.append(f"[bold {badge_color}] {short} [/]")
        # Token count
        if self._tokens:
            diamond = GLYPHS["diamond"]
            if self._tokens >= 1000:
                parts.append(f"[{COLORS['text_dim']}]{diamond} {self._tokens / 1000:.1f}k[/]")
            else:
                parts.append(f"[{COLORS['text_dim']}]{diamond} {self._tokens}[/]")
        # Cost
        if self._cost > 0:
            parts.append(f"[{COLORS['accent2']}]${self._cost:.4f}[/]")
        # $/hr rate
        if self._rate > 0:
            parts.append(f"[{COLORS['text_dim']}]${self._rate:.2f}/hr[/]")
        # Elapsed with pulse
        if self._elapsed:
            pulse = GLYPHS["bullet"] if self._pulse_on else GLYPHS["bullet_empty"]
            pulse_color = COLORS["accent"] if self._pulse_on else COLORS["text_muted"]
            parts.append(f"[{pulse_color}]{pulse}[/] [{COLORS['text_muted']}]{self._elapsed}[/]")

        right = sep.join(parts) if parts else ""
        self.update(f"{left}    {right}")
