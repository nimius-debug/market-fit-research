"""CLI entry points for the scheduled GitHub Actions workflows (ticket 6).

`ingest` wires the real adapters into the daily batch — see _build_sources for
the Reddit source priority; `digest` only needs the Tracker (to refresh
Rejected status) since briefs were already generated and stored during
ingestion. Both commit their own state to the repo from the calling workflow,
not from here (see .github/workflows/).

`social-draft` also pushes the finished draft to the Make.com approval-Sheet
webhook when MAKE_WEBHOOK_URL is set; `social-approve` re-sends a stored
draft to that webhook by hand (recovery after a webhook outage or a Make.com
reconfiguration — posting itself stays gated on approval in the Sheet).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pain_point_pipeline import db, repository
from pain_point_pipeline.adapters.arctic_shift import ArcticShiftSource
from pain_point_pipeline.adapters.claude import ClaudeLLMSearchAdapter
from pain_point_pipeline.adapters.deepseek import DeepSeekLLMSearchAdapter
from pain_point_pipeline.adapters.github_tracker import GitHubTracker
from pain_point_pipeline.adapters.hyperframes_video import HyperFramesVideoAdapter
from pain_point_pipeline.adapters.make_webhook import MakeWebhookAdapter
from pain_point_pipeline.adapters.reddit import RedditSource
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch, run_recluster, run_social_draft
from pain_point_pipeline.ports import LLMSearchPort, SocialQueuePort, SourcePort, VideoRendererPort

DB_PATH = "data/pipeline.sqlite3"
DIGEST_PATH = "DIGEST.md"
SOCIAL_DRAFT_PATH = "SOCIAL_DRAFTS.md"

logger = logging.getLogger(__name__)


def _now() -> datetime:
    # Naive, implicitly UTC — see models.RawItem's created_at convention.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return db.connect(db_path)


def _build_sources() -> list[SourcePort]:
    """The official Reddit API when REDDIT_CLIENT_ID/SECRET are set; else
    ArcticShiftSource, which needs no credentials at all.

    Reddit's Responsible Builder Policy (2026) gates new official-API credentials
    behind a manual approval that is pending for this project (see
    docs/deployment.md); ArcticShiftSource is the zero-configuration default
    until that lands, so ingestion always has a working source out of the box.
    """
    if os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"):
        return [RedditSource()]
    return [ArcticShiftSource()]


def _build_llm() -> LLMSearchPort:
    """DeepSeek by default (far cheaper at this volume); set LLM_PROVIDER=claude
    to use Claude instead — e.g. to get check_competitors' live web search back.
    """
    if os.environ.get("LLM_PROVIDER", "").lower() == "claude":
        llm: LLMSearchPort = ClaudeLLMSearchAdapter()
    else:
        llm = DeepSeekLLMSearchAdapter()
    logger.info("LLM provider: %s (model %s)", type(llm).__name__, getattr(llm, "_model", "unknown"))
    return llm


def _build_social_queue() -> SocialQueuePort | None:
    """The Make.com webhook when MAKE_WEBHOOK_URL is set; else None — local
    runs and repos without the secret just skip queueing, with a log line."""
    if os.environ.get("MAKE_WEBHOOK_URL"):
        return MakeWebhookAdapter()
    logger.info("MAKE_WEBHOOK_URL not set; drafts will not be queued to the approval sheet")
    return None


def _build_video_renderer() -> VideoRendererPort | None:
    """HyperFrames rendering only when SOCIAL_VIDEO_ENABLED is set (the
    workflow sets it, having installed Node/FFmpeg first); local runs skip
    the video entirely — the draft still queues, just text-only."""
    if os.environ.get("SOCIAL_VIDEO_ENABLED", "").lower() == "true":
        return HyperFramesVideoAdapter()
    logger.info("SOCIAL_VIDEO_ENABLED not set; drafts will queue without a video")
    return None


def run_ingestion(db_path: str = DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        sources = _build_sources()
        logger.info("Sources: %s", ", ".join(type(s).__name__ for s in sources))
        llm = _build_llm()
        tracker = GitHubTracker()
        result = run_ingestion_batch(sources, llm, tracker, conn, _now())
        logger.info(
            "Ingestion done: %d new raw items, %d pain points, %d new opportunities, %d issues created",
            result.new_raw_items,
            result.new_pain_points,
            result.new_opportunities,
            len(result.issues_created),
        )
    finally:
        conn.close()


def run_weekly_digest(db_path: str = DB_PATH, digest_path: str = DIGEST_PATH) -> None:
    conn = _connect(db_path)
    try:
        tracker = GitHubTracker()
        result = run_digest_build(conn, tracker, digest_path, _now())
        logger.info(
            "Digest %s: %d opportunities included", result.digest_date, len(result.included_opportunity_ids)
        )
    finally:
        conn.close()


def run_recluster_maintenance(db_path: str = DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        llm = _build_llm()
        tracker = GitHubTracker()
        result = run_recluster(llm, tracker, conn, _now())
        logger.info(
            "Recluster: %d pain points into %d opportunities, %d stale issues closed",
            result.pain_points_reclustered,
            result.opportunities_created,
            result.issues_closed,
        )
    finally:
        conn.close()


def run_social_draft_command(db_path: str = DB_PATH, draft_path: str = SOCIAL_DRAFT_PATH) -> None:
    conn = _connect(db_path)
    try:
        llm = _build_llm()
        queue = _build_social_queue()
        renderer = _build_video_renderer()
        result = run_social_draft(llm, queue, renderer, conn, draft_path, _now())
        if result.opportunity_id is None:
            logger.info("No social draft written this run (empty pool, or none judged worth posting)")
        else:
            logger.info("Social draft written for opportunity %s", result.opportunity_id)
    finally:
        conn.close()


def run_social_approve_command(opportunity_id: str, force: bool = False, db_path: str = DB_PATH) -> int:
    """Re-send a stored draft to the Make.com webhook. Recovery path for a
    webhook that was down (or unset) at draft time — a no-op when the entry
    was already delivered, unless --force."""
    queue = _build_social_queue()
    if queue is None:
        logger.error("social-approve needs MAKE_WEBHOOK_URL to be set")
        return 1
    conn = _connect(db_path)
    try:
        entry = repository.load_social_queue_entry(conn, opportunity_id)
        if entry is None:
            logger.error("No stored social draft for opportunity %s", opportunity_id)
            return 1
        if entry.queued_at is not None and not force:
            logger.info(
                "Draft for %s was already queued at %s; use --force to re-send",
                opportunity_id,
                entry.queued_at.isoformat(),
            )
            return 0
        queue.push(entry)
        repository.mark_social_queued(conn, opportunity_id, _now())
        conn.commit()
        logger.info("Social draft for %s sent to the approval sheet", opportunity_id)
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs one INFO line per request — hundreds per ingestion run; the
    # orchestrator's own per-batch progress lines carry the signal instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="AI/automation pain-point discovery pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="Run the daily ingestion batch against real sources")
    subparsers.add_parser("digest", help="Build the weekly Digest from already-ingested Opportunities")
    subparsers.add_parser(
        "recluster",
        help="Maintenance: re-derive all Opportunities from stored Pain Points under the current match criterion",
    )
    subparsers.add_parser(
        "social-draft",
        help="Pick at most one Opportunity worth a social post, write a draft to SOCIAL_DRAFTS.md, and queue it to the approval sheet",
    )
    approve_parser = subparsers.add_parser(
        "social-approve",
        help="Re-send a stored social draft to the Make.com approval-sheet webhook",
    )
    approve_parser.add_argument("--opportunity-id", required=True)
    approve_parser.add_argument(
        "--force", action="store_true", help="Re-send even if the draft was already queued"
    )
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_ingestion()
    elif args.command == "digest":
        run_weekly_digest()
    elif args.command == "recluster":
        run_recluster_maintenance()
    elif args.command == "social-draft":
        run_social_draft_command()
    elif args.command == "social-approve":
        return run_social_approve_command(args.opportunity_id, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
