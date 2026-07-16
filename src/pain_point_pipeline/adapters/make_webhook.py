"""Real SocialQueuePort adapter: POSTs a finished draft to a Make.com webhook.

The webhook's Make.com scenario appends the draft as a `pending` row to the
approval Google Sheet (see docs/deployment.md, "Social drafts"); a separate
daily scenario posts only rows a human has flipped to `approved`. So this
adapter queues for review — it never publishes anything itself.

The webhook URL comes from the MAKE_WEBHOOK_URL environment variable (a repo
secret in Actions). When it's unset, cli._build_social_queue builds no adapter
at all and the draft run simply skips queueing.
"""

from __future__ import annotations

import os

import requests

from pain_point_pipeline.models import SocialQueueEntry

_TIMEOUT_SECONDS = 30


class MakeWebhookAdapter:
    def __init__(self, webhook_url: str | None = None) -> None:
        resolved = webhook_url or os.environ.get("MAKE_WEBHOOK_URL", "")
        if not resolved:
            raise ValueError("MAKE_WEBHOOK_URL is not set and no webhook_url was given")
        self._webhook_url = resolved

    def push(self, entry: SocialQueueEntry) -> None:
        response = requests.post(
            self._webhook_url,
            json={
                "opportunity_id": entry.opportunity_id,
                "date": entry.date,
                "linkedin_post": entry.linkedin_post,
                "x_thread": entry.x_thread,
                "link": entry.link,
                "video_url": entry.video_url,
            },
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
