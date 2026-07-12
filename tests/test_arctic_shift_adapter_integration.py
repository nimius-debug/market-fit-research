"""Live-API contract test for ArcticShiftSource.

Excluded from the default test run. Run explicitly with:

    pytest -m integration tests/test_arctic_shift_adapter_integration.py

No credentials needed — Arctic Shift's search endpoints are public.
"""

from __future__ import annotations

import pytest
from fakes import FakeLLMSearch, FakeTracker

from pain_point_pipeline.adapters.arctic_shift import ArcticShiftSource
from pain_point_pipeline.orchestrator import run_ingestion_batch

pytestmark = pytest.mark.integration


def test_fetch_new_pulls_at_least_one_real_item_from_each_subreddit() -> None:
    source = ArcticShiftSource(subreddits=["AI_Agents"])

    items = source.fetch_new(since=None)

    assert len(items) > 0
    for item in items:
        assert item.source == "reddit"
        assert item.text
        assert item.url.startswith("https://reddit.com")
        assert item.external_id
        assert item.author


def test_run_ingestion_batch_accepts_real_reddit_data(conn, now) -> None:
    """Proves the orchestrator seam actually accepts ArcticShiftSource, not just fetch_new() in isolation."""
    source = ArcticShiftSource(subreddits=["AI_Agents"])

    result = run_ingestion_batch([source], FakeLLMSearch(), FakeTracker(), conn, now)

    assert result.new_raw_items > 0
