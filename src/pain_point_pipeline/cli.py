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
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pain_point_pipeline import db
from pain_point_pipeline.adapters.arctic_shift import ArcticShiftSource
from pain_point_pipeline.adapters.claude import ClaudeLLMSearchAdapter
from pain_point_pipeline.adapters.github_tracker import GitHubTracker
from pain_point_pipeline.adapters.reddit import RedditSource
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch
from pain_point_pipeline.ports import SourcePort

DB_PATH = "data/pipeline.sqlite3"
DIGEST_PATH = "DIGEST.md"


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


def run_weekly_ingestion(db_path: str = DB_PATH) -> None:
    conn = _connect(db_path)
    try:
        sources = _build_sources()
        llm = ClaudeLLMSearchAdapter()
        tracker = GitHubTracker()
        result = run_ingestion_batch(sources, llm, tracker, conn, _now())
        print(
            f"Ingestion: {result.new_raw_items} new raw items, {result.new_pain_points} pain points, "
            f"{result.new_opportunities} new opportunities, {len(result.issues_created)} issues created"
        )
    finally:
        conn.close()


def run_weekly_digest(db_path: str = DB_PATH, digest_path: str = DIGEST_PATH) -> None:
    conn = _connect(db_path)
    try:
        tracker = GitHubTracker()
        result = run_digest_build(conn, tracker, digest_path, _now())
        print(f"Digest {result.digest_date}: {len(result.included_opportunity_ids)} opportunities included")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI/automation pain-point discovery pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="Run the weekly ingestion batch against real sources")
    subparsers.add_parser("digest", help="Build the weekly Digest from already-ingested Opportunities")
    args = parser.parse_args(argv)

    if args.command == "ingest":
        run_weekly_ingestion()
    elif args.command == "digest":
        run_weekly_digest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
