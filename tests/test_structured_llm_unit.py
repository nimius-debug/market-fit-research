"""Fast, no-network unit tests for StructuredJudgmentAdapter's response handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pain_point_pipeline.adapters._structured_llm import (
    ClusterMatchModel,
    LLMResponseError,
    StructuredJudgmentAdapter,
    SolvabilityJudgementModel,
)


def test_cluster_match_tolerates_an_omitted_field() -> None:
    # DeepSeek omits matched_opportunity_id entirely when it means "no match"
    # (Claude sends an explicit null) — observed live: tool input was {}.
    parsed = ClusterMatchModel.model_validate({})
    assert parsed.matched_opportunity_id is None


def _tool_use_response(tool_name: str, tool_input: dict) -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)
    return SimpleNamespace(stop_reason="tool_use", content=[block])


class _ScriptedClient:
    """Stands in for anthropic.Anthropic; returns one scripted response per call."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = responses
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: object) -> SimpleNamespace:
        response = self._responses[self.calls]
        self.calls += 1
        return response


def _adapter_with(client: _ScriptedClient) -> StructuredJudgmentAdapter:
    adapter = StructuredJudgmentAdapter()
    adapter._client = client  # type: ignore[assignment]
    adapter._model = "test-model"
    return adapter


def test_structured_retries_once_on_a_schema_violating_response() -> None:
    tool = "submit_solvabilityjudgementmodel"
    client = _ScriptedClient(
        [
            _tool_use_response(tool, {}),  # missing both required fields
            _tool_use_response(tool, {"solvable": True, "rationale": "fine on retry"}),
        ]
    )
    adapter = _adapter_with(client)

    parsed = adapter._structured("system", "prompt", SolvabilityJudgementModel)

    assert client.calls == 2
    assert parsed.solvable is True


def test_structured_gives_up_after_a_second_malformed_response() -> None:
    tool = "submit_solvabilityjudgementmodel"
    client = _ScriptedClient([_tool_use_response(tool, {}), _tool_use_response(tool, {})])
    adapter = _adapter_with(client)

    with pytest.raises(Exception):
        adapter._structured("system", "prompt", SolvabilityJudgementModel)

    assert client.calls == 2


def test_structured_retries_when_the_tool_call_is_missing_entirely() -> None:
    tool = "submit_solvabilityjudgementmodel"
    no_tool_call = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="hi")])
    client = _ScriptedClient(
        [no_tool_call, _tool_use_response(tool, {"solvable": False, "rationale": "ok"})]
    )
    adapter = _adapter_with(client)

    parsed = adapter._structured("system", "prompt", SolvabilityJudgementModel)

    assert client.calls == 2
    assert parsed.solvable is False


def test_refusals_are_retried_then_surfaced() -> None:
    refusal = SimpleNamespace(stop_reason="refusal", content=[])
    client = _ScriptedClient([refusal, refusal])
    adapter = _adapter_with(client)

    with pytest.raises(LLMResponseError, match="declined"):
        adapter._structured("system", "prompt", SolvabilityJudgementModel)
