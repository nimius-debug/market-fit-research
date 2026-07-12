"""Real SourcePort adapter: Reddit via RapidAPI's reddit34 third-party wrapper.

Unblocks Reddit ingestion immediately. Reddit's own API (adapters/reddit.py,
RedditSource) remains gated behind the Responsible Builder Policy approval
described in docs/deployment.md; this adapter reads the same public listings
through reddit34 (https://rapidapi.com/.../reddit34), a paid third-party proxy
that fronts Reddit's own JSON. Because it's a reseller, not Reddit itself, its
availability, pricing, and ToS sit outside Reddit's control and can change
without notice — treat it as a stopgap, not a permanent replacement.

Endpoint shapes (verified live 2026-07-11, undocumented otherwise):
`getPostsBySubreddit?subreddit=X&sort=new` -> {"success", "data": {"posts": [{"kind": "t3", "data": {...}}]}}
`getCommentsBySubreddit?subreddit=X` -> {"success", "data": {"comments": [{"kind": "t1", "data": {...}}]}}
Both `data` payloads mirror Reddit's own submission/comment JSON fields.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import requests

from pain_point_pipeline.models import RawItem

BASE_URL = "https://reddit34.p.rapidapi.com"
DEFAULT_HOST = "reddit34.p.rapidapi.com"
DEFAULT_SUBREDDITS = ["AI_Agents", "automation"]


def _to_naive_utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def _headers() -> dict[str, str]:
    try:
        api_key = os.environ["RAPIDAPI_KEY"]
    except KeyError as exc:
        raise RuntimeError("RAPIDAPI_KEY must be set (RapidAPI Hub reddit34 subscription key)") from exc
    host = os.environ.get("RAPIDAPI_HOST", DEFAULT_HOST)
    return {"x-rapidapi-key": api_key, "x-rapidapi-host": host}


def _get_json(session: requests.Session, path: str, params: dict[str, str]) -> dict:
    response = session.get(f"{BASE_URL}/{path}", params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"reddit34 {path} returned an error payload: {payload}")
    return payload["data"]


def _post_to_raw_item(entry: dict) -> RawItem:
    data = entry["data"]
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


def _comment_to_raw_item(entry: dict) -> RawItem:
    data = entry["data"]
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=data["name"],
        author=data.get("author") or "[deleted]",
        url=f"https://reddit.com{data['permalink']}",
        text=data["body"],
        created_at=_to_naive_utc(data["created_utc"]),
    )


class RedditRapidAPISource:
    """Reads new submissions and comments from a fixed set of subreddits via reddit34."""

    def __init__(self, subreddits: list[str] | None = None, session: requests.Session | None = None) -> None:
        self.name = "reddit"
        self._subreddits = subreddits or DEFAULT_SUBREDDITS
        self._session = session or requests.Session()
        self._session.headers.update(_headers())

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        items: list[RawItem] = []
        for subreddit in self._subreddits:
            posts = _get_json(self._session, "getPostsBySubreddit", {"subreddit": subreddit, "sort": "new"})
            for entry in posts.get("posts", []):
                item = _post_to_raw_item(entry)
                if since is None or item.created_at > since:
                    items.append(item)

            comments = _get_json(self._session, "getCommentsBySubreddit", {"subreddit": subreddit})
            for entry in comments.get("comments", []):
                item = _comment_to_raw_item(entry)
                if since is None or item.created_at > since:
                    items.append(item)

        return items
