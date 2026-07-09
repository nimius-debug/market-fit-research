"""Real SourcePort adapter: the Roblox DevForum's public Discourse API.

No authentication needed for public category reads, per ADR-0002/CONTEXT.md —
no scraping, just the site's own JSON endpoints. `/latest.json?order=created`
returns newest-first topics; we page until we cross `since` or the page limit.
"""

from __future__ import annotations

import html
import uuid
from datetime import datetime

import requests

from pain_point_pipeline.models import RawItem

BASE_URL = "https://devforum.roblox.com"
_DEFAULT_USER_AGENT = "pain-point-pipeline/0.1 (Roblox/game-dev pain-point discovery)"
_MAX_PAGES = 3


def _parse_created_at(created_at: str) -> datetime:
    # Discourse returns e.g. "2026-07-09T20:24:37.518Z"; store naive UTC (see models.RawItem).
    return datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)


def _original_poster_username(topic: dict, users_by_id: dict[int, str]) -> str:
    posters = topic.get("posters") or []
    if posters:
        user_id = posters[0].get("user_id")
        username = users_by_id.get(user_id)
        if username:
            return username
    return topic.get("last_poster_username", "unknown")


def _topic_to_raw_item(topic: dict, users_by_id: dict[int, str], created_at: datetime) -> RawItem:
    text = topic["title"]
    excerpt = topic.get("excerpt")
    if excerpt:
        text = f"{text}\n\n{html.unescape(excerpt)}"
    return RawItem(
        id=str(uuid.uuid4()),
        source="devforum",
        external_id=str(topic["id"]),
        author=_original_poster_username(topic, users_by_id),
        url=f"{BASE_URL}/t/{topic['slug']}/{topic['id']}",
        text=text,
        created_at=created_at,
    )


class DevForumSource:
    """Reads new topics from the DevForum's site-wide latest-topics feed."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.name = "devforum"
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        items: list[RawItem] = []

        for page in range(_MAX_PAGES):
            response = self._session.get(
                f"{BASE_URL}/latest.json", params={"order": "created", "page": str(page)}, timeout=30
            )
            response.raise_for_status()
            payload = response.json()

            users_by_id = {user["id"]: user["username"] for user in payload.get("users", [])}
            topics = payload["topic_list"]["topics"]
            if not topics:
                break

            reached_known_topics = False
            for topic in topics:
                created_at = _parse_created_at(topic["created_at"])
                if since is not None and created_at <= since:
                    reached_known_topics = True
                    continue
                items.append(_topic_to_raw_item(topic, users_by_id, created_at))

            if since is None or reached_known_topics:
                break

        return items
