"""Onboarding screen for API key setup with polished styling."""

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

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key


class OnboardingScreen(Vertical):
    """First-run screen prompting for an Anthropic API key."""

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

    def compose(self) -> ComposeResult:
        with Center():
            with Middle():
                with Vertical(id="onboard-box"):
                    yield Static(BRAND_ART, classes="brand")
                    yield Static("Enter your Anthropic API key to get started.", classes="info")
                    yield Static("console.anthropic.com/settings/keys", classes="link")
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

        if not CredentialManager.validate_key_format(key):
            feedback.update(f"[{COLORS['error']}]That doesn't look like a valid Anthropic key.[/]")
            return

        feedback.update(f"[{COLORS['warning']}]Validating...[/]")

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=key)
            await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
            feedback.update("")
            self.post_message(OnboardingComplete(api_key=key))
        except anthropic.AuthenticationError:
            feedback.update(f"[{COLORS['error']}]Invalid API key. Check and try again.[/]")
        except Exception as exc:
            feedback.update(f"[{COLORS['error']}]Connection error: {type(exc).__name__}[/]")
