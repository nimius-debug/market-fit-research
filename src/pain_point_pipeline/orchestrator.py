"""Wires Source -> classify -> cluster -> solvability -> brief -> issue, and separately, Digest build.

Two entry points mirror the two GitHub Actions schedules (see docs/specs/0001-v1-pipeline.md):
`run_ingestion_batch` weekly, `run_digest_build` weekly. Both operate purely through the
SourcePort/LLMSearchPort/TrackerPort seams so tests can drive them with fakes.

Ingestion is resumable: the first live 6-subreddit run was killed by the GitHub
Actions job timeout, and because the old design committed once at the very end,
the whole run (including the `since` watermark) was discarded — the next run
would refetch and reclassify everything and time out the same way, forever. Now
the run commits incrementally (after each source's fetch, after each
classification batch, after each opportunity refresh), and classification is
driven by raw_items.processed_at rather than by "inserted this run": whatever a
killed run already classified stays classified, and the next run picks up only
the unprocessed remainder.

Classification — the highest-volume LLM call, one per fetched item — runs
concurrently in a thread pool. Clustering stays sequential on purpose: each new
Pain Point can create the very Opportunity the next one should match into, so
the candidate list must be re-read between items.

Phase 3 (solvability/brief/issue) is resumable and parallel the same way: its
work list comes from opportunities.solvability_checked_at in the DB, not from
an in-memory "touched this run" set — a run that dies mid-phase-3 leaves the
unjudged remainder marked as such, and the next run picks it up. (The first
live run stranded 114 of 181 opportunities exactly this way.) The LLM work per
opportunity runs in the pool; all DB writes and issue creation stay on the
main thread, one commit per opportunity, because the SQLite connection is
single-threaded.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import TypeVar

from pain_point_pipeline import repository
from pain_point_pipeline.digest import MAX_OPPORTUNITIES_PER_DIGEST, append_digest, format_digest_section
from pain_point_pipeline.models import Opportunity, OpportunityBrief, OpportunityIssue, PainPoint
from pain_point_pipeline.ports import (
    BriefNarrative,
    EffortEstimate,
    LLMSearchPort,
    SolvabilityJudgement,
    SourcePort,
    TrackerPort,
)

logger = logging.getLogger(__name__)

# One classification batch = one commit; a killed run loses at most one batch
# of in-flight classifications (they stay unprocessed and are retried next run).
CLASSIFY_BATCH_SIZE = 25
_CLASSIFY_MAX_WORKERS = 8

# Refresh batches are smaller: each opportunity costs up to 4 sequential LLM
# calls, so one batch is already ~30 concurrent-ish requests at the burstiest.
REFRESH_BATCH_SIZE = 8
_REFRESH_MAX_WORKERS = 8

# An issue is the human review surface; a problem one single person mentioned
# once isn't worth a review slot yet. Singletons still get judged and briefed —
# the issue opens automatically if a second Pain Point ever joins. Added when
# the first live run headed toward ~200 issues, nearly all singletons.
MIN_PAIN_POINTS_FOR_ISSUE = 2

_TBatch = TypeVar("_TBatch")


@dataclass
class IngestionResult:
    new_raw_items: int = 0
    new_pain_points: int = 0
    new_opportunities: int = 0
    touched_opportunity_ids: set[str] = field(default_factory=set)
    issues_created: dict[str, int] = field(default_factory=dict)


@dataclass
class DigestResult:
    digest_date: str
    included_opportunity_ids: list[str] = field(default_factory=list)


def _batches(items: list[_TBatch], size: int) -> list[list[_TBatch]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass(frozen=True)
class _RefreshComputation:
    """The pure-LLM half of an opportunity refresh, computed in a worker thread;
    brief fields are None when the judgement came back not solvable."""

    judgement: SolvabilityJudgement
    narrative: BriefNarrative | None = None
    competitor_check: str | None = None
    effort: EffortEstimate | None = None


def _compute_refresh(llm: LLMSearchPort, opportunity: Opportunity) -> _RefreshComputation:
    judgement = llm.judge_solvable(opportunity.pain_points)
    if not judgement.solvable:
        return _RefreshComputation(judgement=judgement)
    narrative = llm.write_brief_narrative(opportunity.pain_points)
    competitor_check = llm.check_competitors(narrative.problem_summary)
    effort = llm.estimate_effort(narrative.problem_summary, narrative.solution_sketch)
    return _RefreshComputation(
        judgement=judgement, narrative=narrative, competitor_check=competitor_check, effort=effort
    )


def run_ingestion_batch(
    sources: list[SourcePort],
    llm: LLMSearchPort,
    tracker: TrackerPort,
    conn: sqlite3.Connection,
    now: datetime,
) -> IngestionResult:
    result = IngestionResult()

    # Phase 1 — fetch. Cheap (no LLM calls); committed per source so the
    # watermark and raw rows survive whatever happens later in the run.
    for source in sources:
        since = repository.get_last_fetched_at(conn, source.name)
        fetched = source.fetch_new(since)
        new_from_source = 0
        for item in fetched:
            if repository.insert_raw_item_if_new(conn, item):
                new_from_source += 1
        result.new_raw_items += new_from_source
        repository.set_last_fetched_at(conn, source.name, now)
        conn.commit()
        logger.info(
            "%s: fetched %d items since %s, %d new", source.name, len(fetched), since or "the beginning", new_from_source
        )

    # Phase 2 — classify (parallel) + cluster (sequential), in committed batches.
    # Reads the unprocessed backlog from the DB, not this run's fetch, so items
    # a killed run left behind are picked up here.
    unprocessed = repository.list_unprocessed_raw_items(conn)
    batches = _batches(unprocessed, CLASSIFY_BATCH_SIZE)
    logger.info(
        "Classifying %d unprocessed items in %d batches of up to %d",
        len(unprocessed),
        len(batches),
        CLASSIFY_BATCH_SIZE,
    )
    done = 0
    with ThreadPoolExecutor(max_workers=_CLASSIFY_MAX_WORKERS) as pool:
        for batch in batches:
            classifications = list(pool.map(llm.classify_pain_point, batch))

            for item, classification in zip(batch, classifications):
                if classification.is_pain_point:
                    pain_point_id = str(uuid.uuid4())
                    pain_point = PainPoint(
                        id=pain_point_id, raw_item=item, summary=classification.summary, created_at=now
                    )
                    repository.insert_pain_point(conn, pain_point)
                    result.new_pain_points += 1

                    candidates = repository.list_open_opportunity_summaries(conn)
                    match = llm.match_or_create_opportunity(item, candidates)
                    opportunity_id = match.opportunity_id
                    if opportunity_id is None:
                        opportunity_id = str(uuid.uuid4())
                        repository.create_opportunity(conn, opportunity_id, title=classification.summary, now=now)
                        result.new_opportunities += 1

                    repository.add_pain_point_to_opportunity(conn, opportunity_id, pain_point_id, now)
                    result.touched_opportunity_ids.add(opportunity_id)

                repository.mark_raw_item_processed(conn, item.id, now)

            conn.commit()
            done += len(batch)
            logger.info(
                "Progress: %d/%d items classified — %d pain points, %d opportunities so far (committed)",
                done,
                len(unprocessed),
                result.new_pain_points,
                result.new_opportunities,
            )

    # Phase 3 — solvability/brief/issue, DB-driven like phase 2: the work list
    # includes opportunities stranded unjudged by an earlier killed run, not
    # just ones touched now. LLM calls run in the pool; DB writes and issue
    # creation stay on this thread (SQLite connection is single-threaded),
    # committed per opportunity.
    pending_ids = repository.opportunities_needing_refresh(conn)
    logger.info(
        "Refreshing solvability/briefs for %d opportunities (%d touched this run, rest backlog)",
        len(pending_ids),
        len(result.touched_opportunity_ids),
    )
    refreshed = 0
    with ThreadPoolExecutor(max_workers=_REFRESH_MAX_WORKERS) as pool:
        for id_batch in _batches(pending_ids, REFRESH_BATCH_SIZE):
            opportunities = [repository.load_opportunity(conn, oid) for oid in id_batch]
            computations = list(pool.map(partial(_compute_refresh, llm), opportunities))
            for opportunity, computation in zip(opportunities, computations):
                _apply_refresh(opportunity, computation, tracker, conn, now, result)
                conn.commit()
            refreshed += len(id_batch)
            logger.info("Refresh progress: %d/%d opportunities (committed)", refreshed, len(pending_ids))

    _refresh_all_issue_statuses(tracker=tracker, conn=conn, now=now)

    conn.commit()
    return result


def _apply_refresh(
    opportunity: Opportunity,
    computation: _RefreshComputation,
    tracker: TrackerPort,
    conn: sqlite3.Connection,
    now: datetime,
    result: IngestionResult,
) -> None:
    judgement = computation.judgement
    repository.update_opportunity_solvability(
        conn, opportunity.id, judgement.solvable, judgement.rationale, checked_at=now
    )
    logger.info(
        "Opportunity %r (%d pain points): solvable=%s", opportunity.title[:70], opportunity.frequency, judgement.solvable
    )

    if not judgement.solvable:
        return
    assert computation.narrative is not None
    assert computation.competitor_check is not None
    assert computation.effort is not None

    brief = OpportunityBrief(
        opportunity_id=opportunity.id,
        problem_summary=computation.narrative.problem_summary,
        solution_sketch=computation.narrative.solution_sketch,
        effort_size=computation.effort.size,
        effort_rationale=computation.effort.rationale,
        competitor_check=computation.competitor_check,
        generated_at=now,
    )
    repository.save_brief(conn, brief)

    if opportunity.frequency >= MIN_PAIN_POINTS_FOR_ISSUE and repository.load_issue(conn, opportunity.id) is None:
        issue_number = tracker.create_issue(opportunity.id, brief, title=opportunity.title)
        repository.save_issue(
            conn, OpportunityIssue(opportunity_id=opportunity.id, issue_number=issue_number, status="open", checked_at=now)
        )
        result.issues_created[opportunity.id] = issue_number
        logger.info("Opened issue #%d for %r", issue_number, opportunity.title[:70])


def _refresh_all_issue_statuses(tracker: TrackerPort, conn: sqlite3.Connection, now: datetime) -> None:
    for opportunity_id in repository.list_tracked_opportunity_ids(conn):
        issue = repository.load_issue(conn, opportunity_id)
        assert issue is not None
        status = tracker.get_status(issue.issue_number)
        if status != issue.status:
            repository.save_issue(
                conn,
                OpportunityIssue(
                    opportunity_id=opportunity_id, issue_number=issue.issue_number, status=status, checked_at=now
                ),
            )


def run_digest_build(conn: sqlite3.Connection, tracker: TrackerPort, digest_path: str, now: datetime) -> DigestResult:
    digest_date = now.date().isoformat()
    _refresh_all_issue_statuses(tracker=tracker, conn=conn, now=now)

    # already ranked by frequency desc (see repository.solvable_undigested_opportunity_ids)
    candidate_ids = repository.solvable_undigested_opportunity_ids(conn)
    included_ids = candidate_ids[:MAX_OPPORTUNITIES_PER_DIGEST]

    entries = []
    for opportunity_id in included_ids:
        opportunity = repository.load_opportunity(conn, opportunity_id)
        brief = repository.load_brief(conn, opportunity_id)
        assert brief is not None
        entries.append((opportunity, brief))
        repository.mark_digested(conn, opportunity_id, digest_date, now)

    section = format_digest_section(digest_date, entries)
    append_digest(digest_path, section)

    conn.commit()
    return DigestResult(digest_date=digest_date, included_opportunity_ids=included_ids)
