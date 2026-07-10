"""Fast, no-network unit tests for the Claude adapter's model handling."""

from __future__ import annotations

import pytest

from pain_point_pipeline.adapters.claude import DEFAULT_MODEL, _web_search_tool_type, model_from_env


def test_default_model_is_the_cheap_tier() -> None:
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_claude_model_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-8")
    assert model_from_env() == "claude-opus-4-8"


def test_haiku_uses_the_basic_web_search_variant() -> None:
    assert _web_search_tool_type("claude-haiku-4-5") == "web_search_20250305"


@pytest.mark.parametrize(
    "model", ["claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5"]
)
def test_capable_models_use_the_dynamic_filtering_variant(model: str) -> None:
    assert _web_search_tool_type(model) == "web_search_20260209"
