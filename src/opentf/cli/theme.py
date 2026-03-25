"""OpenTF color scheme, themes, and styling constants."""

from __future__ import annotations

# --- Theme palettes ---

THEMES: dict[str, dict[str, str]] = {
    "gruvbox": {
        "bg":           "#1d2021",
        "surface":      "#282828",
        "panel":        "#32302f",
        "border":       "#3c3836",
        "border_focus": "#fabd2f",
        "text":         "#ebdbb2",
        "text_dim":     "#bdae93",
        "text_muted":   "#7c6f64",
        "accent":       "#fe8019",
        "accent2":      "#8ec07c",
        "success":      "#b8bb26",
        "error":        "#fb4934",
        "warning":      "#fabd2f",
        "user_msg":     "#fe8019",
        "assistant_msg":"#83a598",
    },
    "monokai": {
        "bg":           "#272822",
        "surface":      "#2e2e2a",
        "panel":        "#3e3d32",
        "border":       "#49483e",
        "border_focus": "#e6db74",
        "text":         "#f8f8f2",
        "text_dim":     "#a6a28c",
        "text_muted":   "#75715e",
        "accent":       "#f92672",
        "accent2":      "#a6e22e",
        "success":      "#a6e22e",
        "error":        "#f92672",
        "warning":      "#e6db74",
        "user_msg":     "#66d9ef",
        "assistant_msg":"#ae81ff",
    },
    "solarized": {
        "bg":           "#002b36",
        "surface":      "#073642",
        "panel":        "#073642",
        "border":       "#586e75",
        "border_focus": "#b58900",
        "text":         "#839496",
        "text_dim":     "#657b83",
        "text_muted":   "#586e75",
        "accent":       "#cb4b16",
        "accent2":      "#2aa198",
        "success":      "#859900",
        "error":        "#dc322f",
        "warning":      "#b58900",
        "user_msg":     "#268bd2",
        "assistant_msg":"#2aa198",
    },
    "catppuccin": {
        "bg":           "#1e1e2e",
        "surface":      "#313244",
        "panel":        "#45475a",
        "border":       "#585b70",
        "border_focus": "#f5c2e7",
        "text":         "#cdd6f4",
        "text_dim":     "#a6adc8",
        "text_muted":   "#6c7086",
        "accent":       "#cba6f7",
        "accent2":      "#94e2d5",
        "success":      "#a6e3a1",
        "error":        "#f38ba8",
        "warning":      "#f9e2af",
        "user_msg":     "#89b4fa",
        "assistant_msg":"#cba6f7",
    },
    "tokyo-night": {
        "bg":           "#1a1b26",
        "surface":      "#24283b",
        "panel":        "#292e42",
        "border":       "#3b4261",
        "border_focus": "#7aa2f7",
        "text":         "#c0caf5",
        "text_dim":     "#a9b1d6",
        "text_muted":   "#565f89",
        "accent":       "#ff9e64",
        "accent2":      "#73daca",
        "success":      "#9ece6a",
        "error":        "#f7768e",
        "warning":      "#e0af68",
        "user_msg":     "#7aa2f7",
        "assistant_msg":"#bb9af7",
    },
    "minimal": {
        "bg":           "#000000",
        "surface":      "#111111",
        "panel":        "#1a1a1a",
        "border":       "#333333",
        "border_focus": "#ffffff",
        "text":         "#cccccc",
        "text_dim":     "#888888",
        "text_muted":   "#555555",
        "accent":       "#ffffff",
        "accent2":      "#aaaaaa",
        "success":      "#00cc00",
        "error":        "#cc0000",
        "warning":      "#cccc00",
        "user_msg":     "#ffffff",
        "assistant_msg":"#aaaaaa",
    },
}

# --- Active theme ---

_active_theme: str = "gruvbox"


def get_theme() -> str:
    """Return the name of the active theme."""
    return _active_theme


def set_theme(name: str) -> bool:
    """Set the active theme. Returns True if valid, False if unknown."""
    global _active_theme, COLORS
    if name not in THEMES:
        return False
    _active_theme = name
    COLORS.update(THEMES[name])
    return True


def get_colors() -> dict[str, str]:
    """Return the active theme's color palette."""
    return dict(THEMES[_active_theme])


def available_themes() -> list[str]:
    """Return list of available theme names."""
    return list(THEMES.keys())


# Active colors (mutable, updated by set_theme)
COLORS = dict(THEMES[_active_theme])

# Model badge colors (theme-independent)
MODEL_COLORS = {
    "opus":       "#fabd2f",
    "sonnet":     "#d3869b",
    "haiku":      "#8ec07c",
    "gpt-4o":     "#74b9ff",
    "gpt-4o-mini":"#a29bfe",
    "o1":         "#fd79a8",
    "llama3.1":       "#55efc4",
    "codellama":      "#00cec9",
    "mistral":        "#ffeaa7",
    "qwen2.5-coder":  "#81ecec",
}

# Spinner frames
SPINNERS = {
    "dots":       ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    "thinking":   ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    "done":       ["✓"],
    "error":      ["✗"],
}

# Per-model pricing (per million tokens)
MODEL_PRICING = {
    "opus":   {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0,  "output": 15.0},
    "haiku":  {"input": 0.80, "output": 4.0},
    "gpt-4o":      {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o1":          {"input": 15.0, "output": 60.0},
    "llama3.1":       {"input": 0.0, "output": 0.0},
    "codellama":      {"input": 0.0, "output": 0.0},
    "mistral":        {"input": 0.0, "output": 0.0},
    "qwen2.5-coder":  {"input": 0.0, "output": 0.0},
}

SONNET_INPUT_PRICE = MODEL_PRICING["sonnet"]["input"]
SONNET_OUTPUT_PRICE = MODEL_PRICING["sonnet"]["output"]
