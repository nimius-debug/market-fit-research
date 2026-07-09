"""Live-API contract tests for ClaudeLLMSearchAdapter (ticket 4).

Excluded from the default test run (see pyproject.toml `addopts`). Run explicitly with:

    pytest -m integration tests/test_claude_adapter_integration.py

Requires ANTHROPIC_API_KEY (or another credential source the Anthropic SDK
resolves automatically) in the environment.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

import pytest

from pain_point_pipeline.adapters.claude import ClaudeLLMSearchAdapter
from pain_point_pipeline.models import OpportunitySummary, PainPoint, RawItem

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="requires ANTHROPIC_API_KEY"
    ),
]


def _make_item(text: str) -> RawItem:
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=str(uuid.uuid4()),
        author="alice",
        url="https://example.com/reddit/x",
        text=text,
        created_at=datetime(2026, 7, 9, 12, 0, 0),
    )


@pytest.fixture
def adapter() -> ClaudeLLMSearchAdapter:
    return ClaudeLLMSearchAdapter()


def test_classify_pain_point_recognizes_a_real_pain_point(adapter: ClaudeLLMSearchAdapter) -> None:
    item = _make_item(
        "Every time I try to publish my Roblox game, the asset upload times out and I have to "
        "retry five or six times. There's no way to batch-upload assets in Studio, so this eats "
        "an hour every release. Anyone found a workaround?"
    )
    result = adapter.classify_pain_point(item)
    assert result.is_pain_point is True
    assert result.summary


def test_classify_pain_point_rejects_generic_venting(adapter: ClaudeLLMSearchAdapter) -> None:
    item = _make_item("lol Roblox is wild today")
    result = adapter.classify_pain_point(item)
    assert result.is_pain_point is False


def test_match_or_create_opportunity_matches_the_same_underlying_problem(
    adapter: ClaudeLLMSearchAdapter,
) -> None:
    candidate = OpportunitySummary(
        id="opp-1", title="Asset upload times out repeatedly during Studio publish"
    )
    item = _make_item(
        "Publishing keeps failing because the asset upload step times out over and over — "
        "same issue as everyone else is describing with Studio publish."
    )
    match = adapter.match_or_create_opportunity(item, [candidate])
    assert match.opportunity_id == "opp-1"


def test_judge_solvable_accepts_a_solo_dev_buildable_problem(adapter: ClaudeLLMSearchAdapter) -> None:
    pain_points = [
        PainPoint(
            id=str(uuid.uuid4()),
            raw_item=_make_item("I wish there was a tool to batch-upload Roblox assets."),
            summary="No batch asset upload tool for Roblox Studio publishing.",
            created_at=datetime(2026, 7, 9, 12, 0, 0),
        )
    ]
    judgement = adapter.judge_solvable(pain_points)
    assert judgement.solvable is True
    assert judgement.rationale


def test_judge_solvable_rejects_a_platform_only_problem(adapter: ClaudeLLMSearchAdapter) -> None:
    pain_points = [
        PainPoint(
            id=str(uuid.uuid4()),
            raw_item=_make_item("Roblox Studio itself keeps crashing on startup for everyone."),
            summary="Roblox Studio crashes on startup — an engine-level bug only Roblox Corp can fix.",
            created_at=datetime(2026, 7, 9, 12, 0, 0),
        )
    ]
    judgement = adapter.judge_solvable(pain_points)
    assert judgement.solvable is False


def test_write_brief_narrative_produces_a_summary_and_sketch(adapter: ClaudeLLMSearchAdapter) -> None:
    pain_points = [
        PainPoint(
            id=str(uuid.uuid4()),
            raw_item=_make_item("Asset uploads keep timing out during publish."),
            summary="No batch asset upload tool for Roblox Studio publishing.",
            created_at=datetime(2026, 7, 9, 12, 0, 0),
        )
    ]
    narrative = adapter.write_brief_narrative(pain_points)
    assert narrative.problem_summary
    assert narrative.solution_sketch


def test_check_competitors_returns_a_nonempty_summary(adapter: ClaudeLLMSearchAdapter) -> None:
    summary = adapter.check_competitors(
        "Roblox developers have no reliable way to batch-upload assets during Studio publish."
    )
    assert summary


def test_estimate_effort_returns_a_tshirt_size(adapter: ClaudeLLMSearchAdapter) -> None:
    estimate = adapter.estimate_effort(
        problem_summary="No batch asset upload tool for Roblox Studio publishing.",
        solution_sketch="A CLI/plugin that batches asset uploads via the Roblox Open Cloud API.",
    )
    assert estimate.size in {"S", "M", "L", "XL"}
    assert estimate.rationale
