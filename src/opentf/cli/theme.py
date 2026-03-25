"""OpenTF color scheme and styling constants."""

# Core palette -- dark luxury with minimal accents
COLORS = {
    "bg":           "#0a0a0f",
    "surface":      "#12121a",
    "panel":        "#0e0e16",
    "border":       "#1e1e2a",
    "border_focus": "#7c3aed",
    "text":         "#e2e8f0",
    "text_dim":     "#64748b",
    "text_muted":   "#475569",
    "accent":       "#7c3aed",
    "accent2":      "#06b6d4",
    "success":      "#10b981",
    "error":        "#ef4444",
    "warning":      "#f59e0b",
    "user_msg":     "#7c3aed",
    "assistant_msg":"#06b6d4",
}

# Model badge colors
MODEL_COLORS = {
    "opus":   "#f59e0b",
    "sonnet": "#7c3aed",
    "haiku":  "#06b6d4",
}

# Spinner frames
SPINNERS = {
    "dots":       ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    "thinking":   ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    "done":       ["✓"],
    "error":      ["✗"],
}

# Sonnet pricing (per million tokens)
SONNET_INPUT_PRICE = 3.0
SONNET_OUTPUT_PRICE = 15.0
