"""Fast, no-network unit tests for the DeepSeek adapter's model/credential handling."""

from __future__ import annotations

import pytest

from pain_point_pipeline.adapters.deepseek import DEFAULT_MODEL, DeepSeekLLMSearchAdapter, model_from_env


def test_default_model_is_flash() -> None:
    assert DEFAULT_MODEL == "deepseek-v4-flash"


def test_deepseek_model_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert model_from_env() == "deepseek-v4-pro"


def test_empty_deepseek_model_env_var_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions passes undefined repository variables as empty strings —
    # a live run once sent model="" to the API and got a 400 back.
    monkeypatch.setenv("DEEPSEEK_MODEL", "")
    assert model_from_env() == DEFAULT_MODEL


def test_construction_without_api_key_or_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekLLMSearchAdapter()


def test_construction_with_explicit_client_does_not_need_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    adapter = DeepSeekLLMSearchAdapter(client=object(), model="deepseek-v4-flash")  # type: ignore[arg-type]

    assert adapter._model == "deepseek-v4-flash"
