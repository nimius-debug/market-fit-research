"""Real SourcePort adapter: Reddit via the Arctic Shift API (a community-run
Pushshift successor), read-only, no authentication.

Replaces the RapidAPI reddit34 adapter (removed 2026-07-12), which capped out
at 50 requests/month on its free tier and forced ingestion down to a weekly,
2-subreddit-only cadence. Arctic Shift (https://arctic-shift.photon-reddit.com)
needs no API key and rate-limits around ~120k requests/hour, verified live
2026-07-12 — no meaningful quota at this pipeline's scale. The official Reddit
API (adapters/reddit.py, RedditSource) remains the eventual preferred source
once the Responsible Builder Policy approval described in docs/deployment.md
lands; `cli._build_sources` prefers it over this adapter when its credentials
are set.

Trade-off: community-maintained with "no uptime or performance guarantees" per
its own docs — acceptable here since a missed run is just picked up by the
next one via the `since` watermark. Score/num_comments aren't final until
~36h after posting, which doesn't matter for us since classification only
reads the text.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import requests

from pain_point_pipeline.models import RawItem

BASE_URL = "https://arctic-shift.photon-reddit.com"
DEFAULT_SUBREDDITS = ["AI_Agents", "automation", "artificial", "nocode", "SaaS", "LocalLLaMA"]
_PAGE_LIMIT = 100


def _to_naive_utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _to_epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _post_to_raw_item(data: dict) -> RawItem:
    text = data["title"] if not data.get("selftext") else f"{data['title']}\n\n{data['selftext']}"
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=data["name"],
        author=data.get("author") or "[deleted]",
        url=f"https://reddit.com{data['permalink']}",
        text=text,
        created_at=_to_naive_utc(data["created_utc"]),
    )


def _comment_to_raw_item(data: dict) -> RawItem:
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=data["name"],
        author=data.get("author") or "[deleted]",
        url=f"https://reddit.com{data['permalink']}",
        text=data["body"],
        created_at=_to_naive_utc(data["created_utc"]),
    )


class ArcticShiftSource:
    """Reads new submissions and comments from a fixed set of subreddits via Arctic Shift."""

    def __init__(self, subreddits: list[str] | None = None, session: requests.Session | None = None) -> None:
        self.name = "reddit"
        self._subreddits = subreddits or DEFAULT_SUBREDDITS
        self._session = session or requests.Session()

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        items: list[RawItem] = []
        for subreddit in self._subreddits:
            params: dict[str, str | int] = {"subreddit": subreddit, "limit": _PAGE_LIMIT}
            if since is None:
                params["sort"] = "desc"
            else:
                params["sort"] = "asc"
                params["after"] = _to_epoch(since)

            posts = self._session.get(f"{BASE_URL}/api/posts/search", params=params, timeout=30)
            posts.raise_for_status()
            for data in posts.json()["data"]:
                item = _post_to_raw_item(data)
                if since is None or item.created_at > since:
                    items.append(item)

            comments = self._session.get(f"{BASE_URL}/api/comments/search", params=params, timeout=30)
            comments.raise_for_status()
            for data in comments.json()["data"]:
                item = _comment_to_raw_item(data)
                if since is None or item.created_at > since:
                    items.append(item)

        return items
