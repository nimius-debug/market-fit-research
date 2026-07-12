"""Real SourcePort adapter: Reddit via the official API (PRAW), read-only.

Reads new submissions and comments from the v1 Target Niche's subreddits
(CONTEXT.md: Source). Authenticates as a registered OAuth "script" app —
client_id + client_secret + user_agent gives PRAW a read-only, client-credentials
token; no username/password needed. No scraping, per ADR-0002/CONTEXT.md.

Gated behind Reddit's Responsible Builder Policy approval (see
docs/deployment.md) — until that lands, adapters/reddit_rapidapi.py's
RedditRapidAPISource covers the same subreddits as a paid stopgap.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import praw
import praw.models

from pain_point_pipeline.models import RawItem

DEFAULT_SUBREDDITS = ["AI_Agents", "automation"]
_DEFAULT_USER_AGENT = "pain-point-pipeline/0.1 (AI/automation pain-point discovery)"


def _to_naive_utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)


def build_client() -> praw.Reddit:
    try:
        client_id = os.environ["REDDIT_CLIENT_ID"]
        client_secret = os.environ["REDDIT_CLIENT_SECRET"]
    except KeyError as exc:
        raise RuntimeError(
            "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET must be set (registered OAuth 'script' app)"
        ) from exc
    user_agent = os.environ.get("REDDIT_USER_AGENT", _DEFAULT_USER_AGENT)
    return praw.Reddit(client_id=client_id, client_secret=client_secret, user_agent=user_agent)


def _submission_to_raw_item(submission: praw.models.Submission, created_at: datetime) -> RawItem:
    author = str(submission.author) if submission.author else "[deleted]"
    text = submission.title if not submission.selftext else f"{submission.title}\n\n{submission.selftext}"
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=submission.fullname,
        author=author,
        url=f"https://reddit.com{submission.permalink}",
        text=text,
        created_at=created_at,
    )


def _comment_to_raw_item(comment: praw.models.Comment, created_at: datetime) -> RawItem:
    author = str(comment.author) if comment.author else "[deleted]"
    return RawItem(
        id=str(uuid.uuid4()),
        source="reddit",
        external_id=comment.fullname,
        author=author,
        url=f"https://reddit.com{comment.permalink}",
        text=comment.body,
        created_at=created_at,
    )


class RedditSource:
    """Reads new submissions and comments from a fixed set of subreddits."""

    def __init__(
        self,
        subreddits: list[str] | None = None,
        client: praw.Reddit | None = None,
        limit: int = 100,
    ) -> None:
        self.name = "reddit"
        self._subreddits = subreddits or DEFAULT_SUBREDDITS
        self._client = client or build_client()
        self._limit = limit

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        items: list[RawItem] = []
        for subreddit_name in self._subreddits:
            subreddit = self._client.subreddit(subreddit_name)

            for submission in subreddit.new(limit=self._limit):
                created_at = _to_naive_utc(submission.created_utc)
                if since is not None and created_at <= since:
                    continue
                items.append(_submission_to_raw_item(submission, created_at))

            for comment in subreddit.comments(limit=self._limit):
                created_at = _to_naive_utc(comment.created_utc)
                if since is not None and created_at <= since:
                    continue
                items.append(_comment_to_raw_item(comment, created_at))

        return items
