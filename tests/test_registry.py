"""Tests for the model registry."""

from opentf.llm.registry import (
    get_models,
    get_default_model,
    resolve_model,
    get_model_short_name,
    get_model_pricing,
    get_provider_env_var,
)


def test_get_models_anthropic() -> None:
    models = get_models("anthropic")
    assert "opus-4.6" in models
    assert "sonnet-4.6" in models
    assert "sonnet" in models
    assert "haiku" in models
    assert "haiku-3.5" in models


def test_get_models_openai() -> None:
    models = get_models("openai")
    assert "gpt-4.1" in models
    assert "gpt-4.1-mini" in models
    assert "gpt-4.1-nano" in models
    assert "gpt-4o" in models
    assert "gpt-4o-mini" in models
    assert "o3" in models
    assert "o3-mini" in models
    assert "o4-mini" in models


def test_get_models_ollama() -> None:
    models = get_models("ollama")
    assert "llama3.3" in models
    assert "deepseek-coder-v2" in models
    assert "codellama" in models


def test_get_models_unknown() -> None:
    models = get_models("nonexistent")
    assert models == {}


def test_get_default_model() -> None:
    assert "sonnet" in get_default_model("anthropic")
    assert "4.1" in get_default_model("openai")
    assert "llama3.3" == get_default_model("ollama")
    assert get_default_model("unknown") == ""


def test_resolve_model_short_name() -> None:
    result = resolve_model("anthropic", "opus-4.6")
    assert result == "claude-opus-4-6-20250515"


def test_resolve_model_full_id() -> None:
    result = resolve_model("anthropic", "claude-opus-4-6-20250515")
    assert result == "claude-opus-4-6-20250515"


def test_resolve_model_not_found() -> None:
    result = resolve_model("anthropic", "nonexistent-model")
    assert result is None


def test_resolve_model_openai() -> None:
    result = resolve_model("openai", "gpt-4.1")
    assert result == "gpt-4.1"


def test_get_model_short_name_anthropic() -> None:
    assert get_model_short_name("claude-opus-4-6-20250515") == "opus-4.6"
    assert get_model_short_name("claude-sonnet-4-6-20250514") == "sonnet-4.6"
    assert get_model_short_name("claude-sonnet-4-20250514") == "sonnet"
    assert get_model_short_name("claude-haiku-4-5-20251001") == "haiku"


def test_get_model_short_name_openai() -> None:
    assert get_model_short_name("gpt-4.1") == "gpt-4.1"
    assert get_model_short_name("o3") == "o3"


def test_get_model_short_name_unknown() -> None:
    # Should return some reasonable fallback
    result = get_model_short_name("unknown-model-id")
    assert isinstance(result, str)
    assert len(result) > 0


def test_get_model_pricing_anthropic() -> None:
    pricing = get_model_pricing("claude-sonnet-4-6-20250514")
    assert pricing["input"] == 3.0
    assert pricing["output"] == 15.0


def test_get_model_pricing_openai() -> None:
    pricing = get_model_pricing("gpt-4.1")
    assert pricing["input"] == 2.0
    assert pricing["output"] == 8.0


def test_get_model_pricing_ollama() -> None:
    pricing = get_model_pricing("llama3.3")
    assert pricing["input"] == 0.0
    assert pricing["output"] == 0.0


def test_get_model_pricing_unknown() -> None:
    pricing = get_model_pricing("unknown-model")
    assert pricing["input"] == 0.0
    assert pricing["output"] == 0.0


def test_get_provider_env_var() -> None:
    assert get_provider_env_var("anthropic") == "ANTHROPIC_API_KEY"
    assert get_provider_env_var("openai") == "OPENAI_API_KEY"
    assert get_provider_env_var("ollama") == ""
