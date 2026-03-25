"""Chat-style output display with message blocks and thinking indicator."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, Static

from opentf.cli.theme import COLORS


class ThinkingIndicator(Static):
    """Animated thinking dots shown during LLM processing."""

    DEFAULT_CSS = f"""
    ThinkingIndicator {{
        height: 1;
        padding: 0 2;
        color: {COLORS['text_muted']};
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._dots = 0
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.4, self._tick)
        self._update_display()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self._update_display()

    def _update_display(self) -> None:
        dots = "." * self._dots + " " * (3 - self._dots)
        self.update(f"[{COLORS['text_muted']}]  thinking{dots}[/]")


class MessageBlock(Static):
    """A single chat message with role-based left border."""

    DEFAULT_CSS = f"""
    MessageBlock {{
        padding: 0 1 0 2;
        margin: 0;
        color: {COLORS['text']};
    }}
    MessageBlock.user-msg {{
        border-left: thick {COLORS['user_msg']};
        margin-top: 1;
        padding: 0 1 0 1;
    }}
    MessageBlock.assistant-msg {{
        border-left: thick {COLORS['assistant_msg']};
        padding: 0 1 0 1;
    }}
    MessageBlock.system-msg {{
        color: {COLORS['text_dim']};
        padding-left: 2;
    }}
    """

    def __init__(self, text: str = "", role: str = "system", **kwargs) -> None:
        # Pass text directly to Static so it renders immediately
        super().__init__(text, classes=f"{role}-msg", **kwargs)


class StreamBlock(Vertical):
    """A streaming assistant message that updates a Markdown widget live."""

    DEFAULT_CSS = f"""
    StreamBlock {{
        height: auto;
        border-left: thick {COLORS['assistant_msg']};
        padding: 0 1 0 1;
        margin: 0;
    }}
    StreamBlock Markdown {{
        margin: 0;
        padding: 0;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._content = ""
        self._md: Markdown | None = None

    def compose(self) -> ComposeResult:
        self._md = Markdown("")
        yield self._md

    async def append(self, text: str) -> None:
        self._content += text
        if self._md:
            await self._md.update(self._content)


class OutputDisplay(VerticalScroll):
    """Scrollable chat-style output with user/assistant/system messages."""

    DEFAULT_CSS = f"""
    OutputDisplay {{
        background: {COLORS['bg']};
        padding: 0 1;
        scrollbar-size: 1 1;
    }}
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_stream: StreamBlock | None = None
        self._thinking: ThinkingIndicator | None = None

    async def add_user_message(self, text: str) -> None:
        """Add a user message with purple left border."""
        await self.hide_thinking()
        block = MessageBlock(f"[bold]{text}[/]", role="user")
        await self.mount(block)
        self.scroll_end(animate=False)

    async def start_stream(self) -> None:
        """Begin a streaming assistant message."""
        await self.hide_thinking()
        self._current_stream = StreamBlock()
        await self.mount(self._current_stream)

    async def append_stream(self, text: str) -> None:
        """Append text to the current streaming block."""
        if self._current_stream:
            await self._current_stream.append(text)
            self.scroll_end(animate=False)

    async def end_stream(self) -> None:
        """Finalize the current stream."""
        self._current_stream = None

    async def show_thinking(self) -> None:
        """Show the animated thinking indicator."""
        if self._thinking:
            return
        self._thinking = ThinkingIndicator()
        await self.mount(self._thinking)
        self.scroll_end(animate=False)

    async def hide_thinking(self) -> None:
        """Remove the thinking indicator."""
        if self._thinking:
            await self._thinking.remove()
            self._thinking = None

    # --- Legacy / system message methods ---

    async def append_text(self, text: str) -> None:
        """Add a system-style message block (used for commands, errors, info)."""
        if not text or text == "\n":
            return
        block = MessageBlock(text, role="system")
        await self.mount(block)
        self.scroll_end(animate=False)

    async def set_text(self, text: str) -> None:
        """Replace all content with a single message."""
        await self.clear_output()
        if text:
            await self.append_text(text)

    async def clear_output(self) -> None:
        """Remove all message blocks."""
        self._current_stream = None
        await self.hide_thinking()
        children = list(self.children)
        for child in children:
            await child.remove()
