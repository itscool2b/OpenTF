"""Reusable visual rendering functions for Rich markup output.

Pure functions -- no state, no widgets. Used by both REPL and TUI modes.
"""

from __future__ import annotations

from opentf.cli.theme import (
    BORDERS, COLORS, GLYPHS, GRADIENT, GRADIENT_STOPS,
    MODEL_COLORS, PROGRESS_CHARS, SPINNERS,
    get_border_style, gradient_text,
)

# --- ASCII art logo ---

_LOGO_LINES = [
    r"   ___                   _____ _____",
    r"  / _ \ _ __   ___ _ _ |_   _|  ___|",
    r" | | | | '_ \ / _ \ '_ \ | | | |_  ",
    r" | |_| | |_) |  __/ | | || | |  _| ",
    r"  \___/| .__/ \___|_| |_||_| |_|   ",
    r"       |_|                          ",
]


def banner_art(version: str = "0.1.0") -> str:
    """Return the OpenTF ASCII art logo with gradient coloring."""
    lines: list[str] = []
    for line in _LOGO_LINES:
        lines.append(gradient_text(line))
    info = (
        f"  [{COLORS['text_dim']}]v{version}[/]"
        f"  [{COLORS['text_muted']}]{GLYPHS['dot']}[/]"
        f"  [{COLORS['text_dim']}]Task Force CLI[/]"
    )
    lines.append(info)
    return "\n".join(lines)


def section_header(title: str, width: int = 50, color: str | None = None) -> str:
    """Return a styled section header with box corners and gradient title.

    Example output:
        ╭─ Planning Mode ────────────────────╮
    """
    style = get_border_style()
    b = BORDERS[style]
    c = color or COLORS["accent"]
    grad_title = gradient_text(title)
    # Calculate padding: width - corners(2) - spaces around title(2) - dashes(2) - title len
    pad = max(width - 4 - len(title) - 2, 0)
    return (
        f"[{c}]{b['tl']}{b['h']}[/] {grad_title} "
        f"[{c}]{b['h'] * pad}{b['tr']}[/]"
    )


def section_footer(width: int = 50, color: str | None = None) -> str:
    """Return a closing section footer line."""
    style = get_border_style()
    b = BORDERS[style]
    c = color or COLORS["border"]
    return f"[{c}]{b['bl']}{b['h'] * (width - 2)}{b['br']}[/]"


def animated_progress_bar(
    done: int,
    total: int,
    width: int = 25,
    frame: int = 0,
    filled_color: str | None = None,
    empty_color: str | None = None,
) -> str:
    """Return a Rich-markup progress bar with shimmer effect on leading edge.

    The leading edge character cycles through PROGRESS_CHARS based on frame.
    """
    if total <= 0:
        return ""
    fc = filled_color or COLORS["success"]
    ec = empty_color or COLORS["text_muted"]
    ratio = min(done / total, 1.0)
    filled = int(ratio * width)
    empty = width - filled

    bar_filled = PROGRESS_CHARS[3] * filled  # full blocks
    if empty > 0:
        # Shimmer on the leading edge
        leading = PROGRESS_CHARS[frame % len(PROGRESS_CHARS)]
        bar_empty = leading + PROGRESS_CHARS[0] * max(empty - 1, 0)
    else:
        bar_empty = ""

    pct = f"{ratio * 100:.0f}%"
    return (
        f"[{fc}]{bar_filled}[/]"
        f"[{ec}]{bar_empty}[/]"
        f" [{COLORS['text_dim']}]{pct}[/]"
    )


def status_badge(label: str, color: str, bold: bool = True) -> str:
    """Return a compact colored badge like  LABEL .

    Uses Rich markup: colored text on a slightly contrasted background.
    """
    weight = "bold " if bold else ""
    return f"[{weight}{color}] {label} [/]"


def agent_card(
    name: str,
    status: str,
    action: str,
    frame: int = 0,
    width: int = 40,
) -> str:
    """Return a multi-line box-drawn card for a single agent.

    Status-based border color and spinner animation.
    """
    style = get_border_style()
    b = BORDERS[style]

    status_colors = {
        "active":  COLORS["accent"],
        "done":    COLORS["success"],
        "failed":  COLORS["error"],
        "waiting": COLORS["text_muted"],
    }
    status_labels = {
        "active":  "ACTIVE",
        "done":    "DONE",
        "failed":  "FAILED",
        "waiting": "WAITING",
    }
    status_spinners = {
        "active":  SPINNERS["pulse"][frame % len(SPINNERS["pulse"])],
        "done":    GLYPHS["check"],
        "failed":  GLYPHS["cross"],
        "waiting": GLYPHS["dot"],
    }

    c = status_colors.get(status, COLORS["text_muted"])
    label = status_labels.get(status, status.upper())
    spinner = status_spinners.get(status, GLYPHS["dot"])

    inner = width - 2
    badge = f"[bold {c}]{label}[/]"
    # Name + padding + badge
    name_part = f" {name} "
    badge_raw = f" {label} "
    pad_len = max(inner - len(name_part) - len(badge_raw) - 2, 0)

    top = (
        f"[{c}]{b['tl']}{b['h']}[/]"
        f"[bold {COLORS['text']}]{name_part}[/]"
        f"[{c}]{b['h'] * pad_len}[/]"
        f" {badge} "
        f"[{c}]{b['tr']}[/]"
    )

    # Action line with spinner
    action_display = f"  {spinner} {action}"
    mid = f"[{c}]{b['v']}[/] [{COLORS['text_dim']}]{action_display:<{inner}}[/][{c}]{b['v']}[/]"

    bottom = f"[{c}]{b['bl']}{b['h'] * inner}{b['br']}[/]"

    return f"{top}\n{mid}\n{bottom}"


def cost_table(
    input_tokens: int,
    output_tokens: int,
    prices: dict[str, float],
    model_short: str,
) -> str:
    """Return a formatted cost breakdown with box borders."""
    style = get_border_style()
    b = BORDERS[style]
    c = COLORS["border"]
    d = COLORS["text_dim"]
    t = COLORS["text"]

    inp_cost = input_tokens * prices.get("input", 0) / 1_000_000
    out_cost = output_tokens * prices.get("output", 0) / 1_000_000
    total = inp_cost + out_cost

    w = 42
    inner = w - 2
    sep = f"[{c}]{b['l']}{b['h'] * inner}{b['r']}[/]"

    title = gradient_text(f" Cost ({model_short})")
    top = f"[{c}]{b['tl']}{b['h']}[/]{title} [{c}]{b['h'] * max(inner - len(f' Cost ({model_short})') - 1, 0)}{b['tr']}[/]"
    bottom = f"[{c}]{b['bl']}{b['h'] * inner}{b['br']}[/]"

    lines = [
        top,
        f"[{c}]{b['v']}[/]  [{d}]input   {input_tokens:>8,} tokens  ${inp_cost:.4f}[/]",
        f"[{c}]{b['v']}[/]  [{d}]output  {output_tokens:>8,} tokens  ${out_cost:.4f}[/]",
        sep,
        f"[{c}]{b['v']}[/]  [bold {t}]total{' ' * 20}${total:.4f}[/]",
        bottom,
    ]
    return "\n".join(lines)


def diff_block(diff_text: str) -> str:
    """Return styled diff text with colored +/- lines."""
    lines: list[str] = []
    for i, line in enumerate(diff_text.split("\n"), 1):
        num = f"[{COLORS['text_muted']}]{i:>3}[/] "
        if line.startswith("+"):
            lines.append(f"{num}[{COLORS['success']}]{line}[/]")
        elif line.startswith("-"):
            lines.append(f"{num}[{COLORS['error']}]{line}[/]")
        elif line.startswith("@@"):
            lines.append(f"{num}[{COLORS['accent2']}]{line}[/]")
        else:
            lines.append(f"{num}[{COLORS['text_dim']}]{line}[/]")
    return "\n".join(lines)


def key_badge(key: str, label: str) -> str:
    """Return a key hint badge like '[Esc] cancel'."""
    return (
        f"[{COLORS['surface']} on {COLORS['text_dim']}] {key} [/]"
        f" [{COLORS['text_muted']}]{label}[/]"
    )


def plan_step_icon(status: str, frame: int = 0) -> str:
    """Return a status icon for a plan step."""
    icons = {
        "pending":  f"[{COLORS['text_muted']}]{GLYPHS['bullet_empty']}[/]",
        "running":  f"[{COLORS['accent']}]{SPINNERS['pulse'][frame % 4]}[/]",
        "done":     f"[{COLORS['success']}]{GLYPHS['bullet']}[/]",
        "failed":   f"[{COLORS['error']}]{GLYPHS['cross']}[/]",
        "skipped":  f"[{COLORS['text_muted']}]{GLYPHS['bullet_empty']}[/]",
    }
    return icons.get(status, icons["pending"])


def tree_connector(is_last: bool = False) -> str:
    """Return a tree-drawing connector for dependency display."""
    g = GLYPHS
    if is_last:
        return f"[{COLORS['border']}]{g['tree_last']}{BORDERS['rounded']['h']}{g['tree_arrow']}[/]"
    return f"[{COLORS['border']}]{g['tree_branch']}{BORDERS['rounded']['h']}{g['tree_arrow']}[/]"
