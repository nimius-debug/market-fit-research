"""CLI entry points for the two scheduled GitHub Actions workflows (ticket 6).

`ingest` wires the real adapters into the weekly batch — see _build_sources for
the Reddit source priority; `digest` only needs the Tracker (to refresh
Rejected status) since briefs were already generated and stored during
ingestion. Both commit their own state to the repo from the calling workflow,
not from here (see .github/workflows/).

Ingestion still runs weekly (not daily) — that cadence was originally forced by
a since-removed RapidAPI adapter's 50-requests/month quota, and hasn't been
revisited now that ArcticShiftSource has no meaningful rate limit; ask if you
want it moved back to daily.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pain_point_pipeline import db
from pain_point_pipeline.adapters.arctic_shift import ArcticShiftSource
from pain_point_pipeline.adapters.claude import ClaudeLLMSearchAdapter
from pain_point_pipeline.adapters.deepseek import DeepSeekLLMSearchAdapter
from pain_point_pipeline.adapters.github_tracker import GitHubTracker
from pain_point_pipeline.adapters.reddit import RedditSource
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch, run_recluster
from pain_point_pipeline.ports import LLMSearchPort, SourcePort

DB_PATH = "data/pipeline.sqlite3"
DIGEST_PATH = "DIGEST.md"

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


def run_weekly_ingestion(db_path: str = DB_PATH) -> None:
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx logs one INFO line per request — hundreds per ingestion run; the
    # orchestrator's own per-batch progress lines carry the signal instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="AI/automation pain-point discovery pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="Run the weekly ingestion batch against real sources")
    subparsers.add_parser("digest", help="Build the weekly Digest from already-ingested Opportunities")
    subparsers.add_parser(
        "recluster",
        help="Maintenance: re-derive all Opportunities from stored Pain Points under the current match criterion",
    )
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_weekly_ingestion()
    elif args.command == "digest":
        run_weekly_digest()
    elif args.command == "recluster":
        run_recluster_maintenance()
    return 0


if __name__ == "__main__":
    sys.exit(main())
