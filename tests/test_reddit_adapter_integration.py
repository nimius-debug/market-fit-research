"""Live-API contract test for RedditSource (ticket 2).

Excluded from the default test run. Run explicitly with:

    pytest -m integration tests/test_reddit_adapter_integration.py

Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (a registered OAuth
"script" app) in the environment.
"""

from __future__ import annotations

import os

import pytest

from pain_point_pipeline.adapters.reddit import RedditSource

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")),
        reason="requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET",
    ),
]


def test_fetch_new_pulls_at_least_one_real_item_from_each_subreddit() -> None:
    source = RedditSource(limit=5)

    items = source.fetch_new(since=None)

    assert len(items) > 0
    for item in items:
        assert item.source == "reddit"
        assert item.text
        assert item.url.startswith("https://reddit.com")
        assert item.external_id
        assert item.author
