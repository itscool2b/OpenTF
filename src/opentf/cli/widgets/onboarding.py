"""Onboarding screen with provider selection and API key setup."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Static

from opentf.cli.theme import COLORS, GLYPHS, SPINNERS, gradient_text
from opentf.cli.renderables import banner_art


class OnboardingComplete(Message):
    """Posted when the user has entered a valid API key."""

    def __init__(self, api_key: str, provider: str = "anthropic") -> None:
        super().__init__()
        self.api_key = api_key
        self.provider = provider


class OnboardingScreen(Vertical):
    """First-run screen: choose provider, then enter API key."""

    DEFAULT_CSS = f"""
    OnboardingScreen {{
        height: 1fr;
        align: center middle;
    }}
    OnboardingScreen #onboard-box {{
        width: 60;
        height: auto;
        max-height: 26;
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
    OnboardingScreen .provider-buttons {{
        align: center middle;
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }}
    OnboardingScreen Button {{
        margin: 0 1;
        min-width: 16;
    }}
    OnboardingScreen #provider-section {{
        height: auto;
    }}
    OnboardingScreen #key-section {{
        height: auto;
        display: none;
    }}
    """

    def __init__(self, provider: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._provider = provider  # If set, skip provider selection
        self._validating = False
        self._spinner_frame = 0
        self._spinner_timer = None

    def compose(self) -> ComposeResult:
        with Center():
            with Middle():
                with Vertical(id="onboard-box"):
                    yield Static(banner_art(), classes="brand")

                    # Provider selection (shown first if no provider pre-set)
                    with Vertical(id="provider-section"):
                        yield Static("Choose your LLM provider:", classes="info")
                        with Horizontal(classes="provider-buttons"):
                            yield Button("Anthropic", id="btn-anthropic", variant="primary")
                            yield Button("OpenAI", id="btn-openai")
                            yield Button("Ollama", id="btn-ollama")

                    # API key entry (shown after provider is selected)
                    with Vertical(id="key-section"):
                        yield Static("", id="key-info", classes="info")
                        yield Static("", id="key-link", classes="link")
                        yield Input(placeholder="API key (sk-ant-...) or session token", password=True, id="key-input")
                        yield Static("", id="feedback")
                        yield Static("Stored at ~/.config/opentf/credentials.json", classes="dim")

    def on_mount(self) -> None:
        """If provider was pre-set, skip to key entry."""
        if self._provider:
            self._show_key_entry(self._provider)

    def _show_key_entry(self, provider: str) -> None:
        """Transition from provider selection to key entry."""
        self._provider = provider

        try:
            self.query_one("#provider-section").display = False
            key_section = self.query_one("#key-section")
            key_section.display = True
        except Exception:
            return

        if provider == "anthropic":
            info_text = "Enter your API key or Claude session token."
            link_text = "console.anthropic.com/settings/keys"
        elif provider == "openai":
            info_text = "Enter your OpenAI API key to get started."
            link_text = "platform.openai.com/api-keys"
        elif provider == "ollama":
            # Ollama doesn't need a key — just connect
            self.post_message(OnboardingComplete(api_key="", provider="ollama"))
            return
        else:
            info_text = f"Enter your {provider} API key to get started."
            link_text = ""

        try:
            self.query_one("#key-info", Static).update(info_text)
            self.query_one("#key-link", Static).update(link_text)
            self.query_one("#key-input", Input).focus()
        except Exception:
            pass

    @on(Button.Pressed, "#btn-anthropic")
    def on_anthropic(self) -> None:
        self._show_key_entry("anthropic")

    @on(Button.Pressed, "#btn-openai")
    def on_openai(self) -> None:
        self._show_key_entry("openai")

    @on(Button.Pressed, "#btn-ollama")
    def on_ollama(self) -> None:
        self._show_key_entry("ollama")

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
