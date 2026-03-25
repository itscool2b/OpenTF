"""OpenTF color scheme and styling constants."""

# Core palette -- gruvbox dark
COLORS = {
    "bg":           "#1d2021",   # bg0_h  -- deepest background
    "surface":      "#282828",   # bg0    -- elevated surfaces, header
    "panel":        "#32302f",   # bg0_s  -- log, activity panels
    "border":       "#3c3836",   # bg1    -- dividers, inactive borders
    "border_focus": "#fabd2f",   # bright yellow -- focus rings
    "text":         "#ebdbb2",   # fg1    -- primary text
    "text_dim":     "#bdae93",   # fg3    -- secondary text
    "text_muted":   "#7c6f64",   # bg4    -- timestamps, hints
    "accent":       "#fe8019",   # bright orange -- primary action
    "accent2":      "#8ec07c",   # bright aqua   -- secondary, tools
    "success":      "#b8bb26",   # bright green
    "error":        "#fb4934",   # bright red
    "warning":      "#fabd2f",   # bright yellow
    "user_msg":     "#fe8019",   # orange -- user messages
    "assistant_msg":"#83a598",   # bright blue -- assistant messages
}

# Model badge colors
MODEL_COLORS = {
    # Anthropic
    "opus":       "#fabd2f",   # bright yellow (warm gold)
    "sonnet":     "#d3869b",   # bright purple
    "haiku":      "#8ec07c",   # bright aqua
    # OpenAI
    "gpt-4o":     "#74b9ff",   # bright blue
    "gpt-4o-mini":"#a29bfe",   # soft purple
    "o1":         "#fd79a8",   # pink
    # Ollama / local
    "llama3.1":       "#55efc4",   # mint
    "codellama":      "#00cec9",   # teal
    "mistral":        "#ffeaa7",   # pale yellow
    "qwen2.5-coder":  "#81ecec",   # light cyan
}

# Spinner frames
SPINNERS = {
    "dots":       ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    "thinking":   ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    "done":       ["✓"],
    "error":      ["✗"],
}

# Per-model pricing (per million tokens) -- keyed by short name
MODEL_PRICING = {
    # Anthropic
    "opus":   {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0,  "output": 15.0},
    "haiku":  {"input": 0.80, "output": 4.0},
    # OpenAI
    "gpt-4o":      {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o1":          {"input": 15.0, "output": 60.0},
    # Ollama (local, free)
    "llama3.1":       {"input": 0.0, "output": 0.0},
    "codellama":      {"input": 0.0, "output": 0.0},
    "mistral":        {"input": 0.0, "output": 0.0},
    "qwen2.5-coder":  {"input": 0.0, "output": 0.0},
}

# Backward compat aliases
SONNET_INPUT_PRICE = MODEL_PRICING["sonnet"]["input"]
SONNET_OUTPUT_PRICE = MODEL_PRICING["sonnet"]["output"]
