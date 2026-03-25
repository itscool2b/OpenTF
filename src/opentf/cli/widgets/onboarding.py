"""Onboarding screen for API key setup with multi-provider support."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Middle, Vertical
from textual.message import Message
from textual.widgets import Input, Static

from opentf.cli.theme import COLORS

BRAND_ART = f"""\
[bold {COLORS['text']}]
  ___                 _____ _____
 / _ \\ _ __  ___ _ _ |_   _|  ___|
| | | | '_ \\/ -_) ' \\  | | | |_
| |_| | .__/\\___|_||_| |_| |  _|
 \\___/|_|                  |_|
[/]"""


class OnboardingComplete(Message):
    """Posted when the user has entered a valid API key."""

    def __init__(self, api_key: str, provider: str = "anthropic") -> None:
        super().__init__()
        self.api_key = api_key
        self.provider = provider


class OnboardingScreen(Vertical):
    """First-run screen prompting for an API key."""

    DEFAULT_CSS = f"""
    OnboardingScreen {{
        height: 1fr;
        align: center middle;
    }}
    OnboardingScreen #onboard-box {{
        width: 56;
        height: auto;
        max-height: 20;
        padding: 1 3;
        border: solid {COLORS['border']};
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
                    yield Static(BRAND_ART, classes="brand")
                    yield Static(info_text, classes="info")
                    if link_text:
                        yield Static(link_text, classes="link")
                    yield Input(placeholder="Paste your API key here", password=True, id="key-input")
                    yield Static("", id="feedback")
                    yield Static("Stored at ~/.config/opentf/credentials.json", classes="dim")

    @on(Input.Submitted, "#key-input")
    async def on_key_submitted(self, event: Input.Submitted) -> None:
        key = event.value.strip()
        feedback = self.query_one("#feedback", Static)

        if not key:
            feedback.update(f"[{COLORS['error']}]Please enter an API key.[/]")
            return

        from opentf.auth.credentials import CredentialManager

        if not CredentialManager.validate_key_format(key, self._provider):
            feedback.update(f"[{COLORS['error']}]That doesn't look like a valid {self._provider} key.[/]")
            return

        feedback.update(f"[{COLORS['warning']}]Validating...[/]")

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
                # For other providers, just accept the key
                pass

            feedback.update("")
            self.post_message(OnboardingComplete(api_key=key, provider=self._provider))
        except Exception as exc:
            exc_name = type(exc).__name__
            if "authentication" in exc_name.lower() or "auth" in str(exc).lower():
                feedback.update(f"[{COLORS['error']}]Invalid API key. Check and try again.[/]")
            else:
                feedback.update(f"[{COLORS['error']}]Connection error: {exc_name}[/]")
