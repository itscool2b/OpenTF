"""Model registry: maps provider + short name to model ID and pricing.

Centralizes model metadata so that the UI, cost calculation, and
provider backends all use a single source of truth.
"""

from __future__ import annotations


MODEL_REGISTRY: dict[str, dict[str, str]] = {
    "anthropic": {
        "opus-4.6":   "claude-opus-4-6-20250515",
        "sonnet-4.6": "claude-sonnet-4-6-20250514",
        "sonnet":     "claude-sonnet-4-20250514",
        "haiku":      "claude-haiku-4-5-20251001",
        "haiku-3.5":  "claude-3-5-haiku-20241022",
    },
    "openai": {
        "gpt-4.1":      "gpt-4.1",
        "gpt-4.1-mini": "gpt-4.1-mini",
        "gpt-4.1-nano": "gpt-4.1-nano",
        "gpt-4o":       "gpt-4o",
        "gpt-4o-mini":  "gpt-4o-mini",
        "o3":           "o3",
        "o3-mini":      "o3-mini",
        "o4-mini":      "o4-mini",
    },
    "ollama": {
        "llama3.3":          "llama3.3",
        "deepseek-coder-v2": "deepseek-coder-v2",
        "codellama":         "codellama",
        "qwen2.5-coder":     "qwen2.5-coder",
        "mistral":           "mistral",
    },
}

# Pricing per million tokens
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6-20250515":    {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6-20250514":  {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-20250514":    {"input": 3.0,   "output": 15.0},
    "claude-haiku-4-5-20251001":   {"input": 0.80,  "output": 4.0},
    "claude-3-5-haiku-20241022":   {"input": 0.80,  "output": 4.0},
    # OpenAI
    "gpt-4.1":      {"input": 2.0,   "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40,  "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10,  "output": 0.40},
    "gpt-4o":       {"input": 2.50,  "output": 10.0},
    "gpt-4o-mini":  {"input": 0.15,  "output": 0.60},
    "o3":           {"input": 10.0,  "output": 40.0},
    "o3-mini":      {"input": 1.10,  "output": 4.40},
    "o4-mini":      {"input": 1.10,  "output": 4.40},
    # Ollama (local, free)
    "llama3.3":          {"input": 0.0, "output": 0.0},
    "deepseek-coder-v2": {"input": 0.0, "output": 0.0},
    "codellama":         {"input": 0.0, "output": 0.0},
    "qwen2.5-coder":     {"input": 0.0, "output": 0.0},
    "mistral":           {"input": 0.0, "output": 0.0},
}

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6-20250514",
    "openai": "gpt-4.1",
    "ollama": "llama3.3",
}

PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "",  # No key needed
}


def get_models(provider: str) -> dict[str, str]:
    """Return {short_name: model_id} for a provider."""
    return dict(MODEL_REGISTRY.get(provider, {}))


def get_default_model(provider: str) -> str:
    """Return the default model ID for a provider."""
    return DEFAULT_MODELS.get(provider, "")


def resolve_model(provider: str, name: str) -> str | None:
    """Resolve a short name to a full model ID for a provider.

    Returns the model ID if found, or None.
    If name is already a full model ID, returns it as-is.
    """
    models = MODEL_REGISTRY.get(provider, {})
    # Check short name first
    if name in models:
        return models[name]
    # Check if it's already a full model ID
    if name in models.values():
        return name
    return None


def get_model_short_name(model_id: str) -> str:
    """Get the short display name for a model ID."""
    for _provider, models in MODEL_REGISTRY.items():
        for short, full in models.items():
            if full == model_id:
                return short
    # Fallback: return last part of model ID
    return model_id.split("/")[-1].split("-")[0] if "/" in model_id or "-" in model_id else model_id


def get_model_pricing(model_id: str) -> dict[str, float]:
    """Get pricing for a model ID. Returns zero pricing if unknown."""
    return MODEL_PRICING.get(model_id, {"input": 0.0, "output": 0.0})


def get_provider_env_var(provider: str) -> str:
    """Return the environment variable name for a provider's API key."""
    return PROVIDER_ENV_VARS.get(provider, "")
