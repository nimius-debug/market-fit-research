"""Live-API contract test for DevForumSource (ticket 3).

Excluded from the default test run. Run explicitly with:

    pytest -m integration tests/test_devforum_adapter_integration.py

No credentials needed — the DevForum's latest-topics feed is public.
"""

from __future__ import annotations

import pytest
from fakes import FakeLLMSearch, FakeTracker

from pain_point_pipeline.adapters.devforum import DevForumSource
from pain_point_pipeline.orchestrator import run_ingestion_batch

pytestmark = pytest.mark.integration


def test_fetch_new_pulls_at_least_one_real_topic() -> None:
    source = DevForumSource()

    items = source.fetch_new(since=None)

    assert len(items) > 0
    for item in items:
        assert item.source == "devforum"
        assert item.text
        assert item.url.startswith("https://devforum.roblox.com/t/")
        assert item.external_id
        assert item.author


def test_run_ingestion_batch_accepts_real_devforum_data(conn, now) -> None:
    """Proves the orchestrator seam actually accepts DevForumSource, not just fetch_new() in isolation."""
    source = DevForumSource()

    result = run_ingestion_batch([source], FakeLLMSearch(), FakeTracker(), conn, now)

    assert result.new_raw_items > 0
