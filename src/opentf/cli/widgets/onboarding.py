"""Onboarding screen for API key setup with gradient logo and animated validation."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Middle, Vertical
from textual.message import Message
from textual.widgets import Input, Static

from opentf.cli.theme import COLORS, GLYPHS, SPINNERS, gradient_text
from opentf.cli.renderables import banner_art


class OnboardingComplete(Message):
    """Posted when the user has entered a valid API key."""

    def __init__(self, api_key: str, provider: str = "anthropic") -> None:
        super().__init__()
        self.api_key = api_key
        self.provider = provider


class OnboardingScreen(Vertical):
    """First-run screen prompting for an API key with gradient branding."""

    DEFAULT_CSS = f"""
    OnboardingScreen {{
        height: 1fr;
        align: center middle;
    }}
    OnboardingScreen #onboard-box {{
        width: 56;
        height: auto;
        max-height: 22;
        padding: 1 3;
        border: round {COLORS['border']};
        background: {COLORS['surface']};
    }}
    OnboardingScreen .brand {{
        text-align: center;
        margin-bottom: 1;
    }}
    OnboardingScreen .info {{
        text-align: center;
        color: {COLORS['text_dim']};
    }}
    OnboardingScreen .link {{
        text-align: center;
        color: {COLORS['accent']};
    }}
    OnboardingScreen .dim {{
        text-align: center;
        color: {COLORS['text_muted']};
        text-style: italic;
    }}
    OnboardingScreen #key-input {{
        margin-top: 1;
        border: solid {COLORS['border']};
    }}
    OnboardingScreen #key-input:focus {{
        border: solid {COLORS['border_focus']};
    }}
    OnboardingScreen #feedback {{
        text-align: center;
        height: 1;
        margin-top: 1;
    }}
    """

    def __init__(self, provider: str = "anthropic", **kwargs) -> None:
        super().__init__(**kwargs)
        self._provider = provider
        self._validating = False
        self._spinner_frame = 0
        self._spinner_timer = None

    def compose(self) -> ComposeResult:
        if self._provider == "anthropic":
            info_text = "Enter your Anthropic API key to get started."
            link_text = "console.anthropic.com/settings/keys"
        elif self._provider == "openai":
            info_text = "Enter your OpenAI API key to get started."
            link_text = "platform.openai.com/api-keys"
        else:
            info_text = f"Enter your {self._provider} API key to get started."
            link_text = ""

        with Center():
            with Middle():
                with Vertical(id="onboard-box"):
                    yield Static(banner_art(), classes="brand")
                    yield Static(info_text, classes="info")
                    if link_text:
                        yield Static(link_text, classes="link")
                    yield Input(placeholder="Paste your API key here", password=True, id="key-input")
                    yield Static("", id="feedback")
                    yield Static("Stored at ~/.config/opentf/credentials.json", classes="dim")

    def _start_spinner(self) -> None:
        self._validating = True
        self._spinner_frame = 0
        self._spinner_timer = self.set_interval(0.15, self._tick_spinner)

    def _stop_spinner(self) -> None:
        self._validating = False
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_spinner(self) -> None:
        self._spinner_frame += 1
        if self._validating:
            frames = SPINNERS["pulse"]
            spinner = frames[self._spinner_frame % len(frames)]
            try:
                feedback = self.query_one("#feedback", Static)
                feedback.update(
                    f"[{COLORS['accent']}]{spinner}[/] [{COLORS['text_dim']}]Validating{GLYPHS['ellipsis']}[/]"
                )
            except Exception:
                pass

    @on(Input.Submitted, "#key-input")
    async def on_key_submitted(self, event: Input.Submitted) -> None:
        key = event.value.strip()
        feedback = self.query_one("#feedback", Static)

        if not key:
            feedback.update(f"[{COLORS['error']}]{GLYPHS['cross']} Please enter an API key.[/]")
            return

        from opentf.auth.credentials import CredentialManager

        if not CredentialManager.validate_key_format(key, self._provider):
            feedback.update(
                f"[{COLORS['error']}]{GLYPHS['cross']} That doesn't look like a valid {self._provider} key.[/]"
            )
            return

        self._start_spinner()

        try:
            if self._provider == "anthropic":
                import anthropic
                client = anthropic.AsyncAnthropic(api_key=key)
                await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "hi"}],
                )
            elif self._provider == "openai":
                import openai
                client = openai.AsyncOpenAI(api_key=key)
                await client.chat.completions.create(
                    model="gpt-4o-mini",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "hi"}],
                )
            else:
                pass

            self._stop_spinner()
            feedback.update(f"[{COLORS['success']}]{GLYPHS['check']} Key validated successfully![/]")
            self.post_message(OnboardingComplete(api_key=key, provider=self._provider))
        except Exception as exc:
            self._stop_spinner()
            exc_name = type(exc).__name__
            if "authentication" in exc_name.lower() or "auth" in str(exc).lower():
                feedback.update(f"[{COLORS['error']}]{GLYPHS['cross']} Invalid API key. Check and try again.[/]")
            else:
                feedback.update(f"[{COLORS['error']}]{GLYPHS['cross']} Connection error: {exc_name}[/]")
