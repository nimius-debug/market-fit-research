"""Wires Source -> classify -> cluster -> solvability -> brief -> issue, and separately, Digest build.

Two entry points mirror the two GitHub Actions schedules (see docs/specs/0001-v1-pipeline.md):
`run_ingestion_batch` daily, `run_digest_build` weekly. Both operate purely through the
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
from pain_point_pipeline.digest import MAX_OPPORTUNITIES_PER_DIGEST, format_digest_section, prepend_digest
from pain_point_pipeline.models import (
    Opportunity,
    OpportunityBrief,
    OpportunityIssue,
    PainPoint,
    SocialQueueEntry,
)
from pain_point_pipeline.ports import (
    BriefNarrative,
    EffortEstimate,
    LLMSearchPort,
    SocialQueuePort,
    SolvabilityJudgement,
    SourcePort,
    TrackerPort,
    VideoRendererPort,
)
from pain_point_pipeline.social import (
    compose_linkedin_post,
    compose_x_thread,
    evidence_link,
    format_social_draft,
    prepend_social_draft,
)
from pain_point_pipeline.video import build_scene_script

logger = logging.getLogger(__name__)

# One classification batch = one commit; a killed run loses at most one batch
# of in-flight classifications (they stay unprocessed and are retried next run).
CLASSIFY_BATCH_SIZE = 25
_CLASSIFY_MAX_WORKERS = 8

# Refresh batches are smaller: each opportunity costs up to 4 sequential LLM
# calls, so one batch is already ~30 concurrent-ish requests at the burstiest.
REFRESH_BATCH_SIZE = 8
_REFRESH_MAX_WORKERS = 8

# Briefs and issues are the human review surface; a problem is only worth a
# review slot once distinct people (not one person posting repeatedly) have hit
# it. Below the gate an Opportunity is still clustered and judged for
# solvability, but the expensive brief trio (narrative/competitor/effort) and
# the GitHub Issue wait until the gate is crossed — both arrive automatically
# on the refresh after the qualifying voice joins. Added when the first live
# runs headed toward ~200 issues, nearly all single-mention singletons.
MIN_DISTINCT_AUTHORS = 3

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


@dataclass
class ReclusterResult:
    pain_points_reclustered: int = 0
    opportunities_created: int = 0
    issues_closed: int = 0


@dataclass
class SocialDraftResult:
    opportunity_id: str | None = None
    """None means no draft was written this run — either the candidate pool
    was empty, or the LLM judged none of it worth posting. Both are normal."""


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
    if not judgement.solvable or opportunity.distinct_authors < MIN_DISTINCT_AUTHORS:
        # Solvability is always judged (one cheap call, keeps the bookkeeping
        # current); the expensive brief trio waits for the author gate.
        return _RefreshComputation(judgement=judgement)
    narrative = llm.write_brief_narrative(opportunity.pain_points)
    competitor_check = llm.check_competitors(narrative.problem_summary)
    effort = llm.estimate_effort(narrative.problem_summary, narrative.solution_sketch)
    return _RefreshComputation(
        judgement=judgement, narrative=narrative, competitor_check=competitor_check, effort=effort
    )


def _issue_title(opportunity: Opportunity) -> str:
    return f"{opportunity.title} ({opportunity.frequency} reports, {opportunity.distinct_authors} people)"


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

                    opportunity_id, created = _assign_to_opportunity(
                        llm, conn, classification.summary, pain_point_id, now
                    )
                    if created:
                        result.new_opportunities += 1
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


def _assign_to_opportunity(
    llm: LLMSearchPort,
    conn: sqlite3.Connection,
    summary: str,
    pain_point_id: str,
    now: datetime,
) -> tuple[str, bool]:
    """Cluster one Pain Point: match its summary into an existing Opportunity or
    create a new one. Returns (opportunity_id, created). Shared by ingestion's
    phase 2 and the recluster replay so both use the identical criterion."""
    candidates = repository.list_match_candidates(conn)
    match = llm.match_or_create_opportunity(summary, candidates)
    opportunity_id = match.opportunity_id
    created = opportunity_id is None
    if opportunity_id is None:
        opportunity_id = str(uuid.uuid4())
        repository.create_opportunity(conn, opportunity_id, title=summary, now=now)
    repository.add_pain_point_to_opportunity(conn, opportunity_id, pain_point_id, now)
    return opportunity_id, created


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
        "Opportunity %r (%d reports, %d people): solvable=%s",
        opportunity.title[:70],
        opportunity.frequency,
        opportunity.distinct_authors,
        judgement.solvable,
    )

    if computation.narrative is None:
        # Not solvable, or below the author gate — judged and recorded, but no
        # brief or issue until enough distinct voices join.
        return
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
        user_flow=computation.narrative.user_flow,
    )
    repository.save_brief(conn, brief)

    issue = repository.load_issue(conn, opportunity.id)
    if issue is None:
        issue_number = tracker.create_issue(opportunity.id, brief, title=_issue_title(opportunity))
        repository.save_issue(
            conn, OpportunityIssue(opportunity_id=opportunity.id, issue_number=issue_number, status="open", checked_at=now)
        )
        result.issues_created[opportunity.id] = issue_number
        logger.info("Opened issue #%d for %r", issue_number, opportunity.title[:70])
    else:
        # Keep the counts in the title current — they're the scan signal.
        tracker.update_issue_title(issue.issue_number, _issue_title(opportunity))


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
    prepend_digest(digest_path, section)

    conn.commit()
    return DigestResult(digest_date=digest_date, included_opportunity_ids=included_ids)


def run_social_draft(
    llm: LLMSearchPort,
    queue: SocialQueuePort | None,
    renderer: VideoRendererPort | None,
    conn: sqlite3.Connection,
    draft_path: str,
    now: datetime,
) -> SocialDraftResult:
    """Pick at most one Opportunity per run for a social post: from
    repository.list_viral_candidates (>5 reports, solvable, briefed, never
    used before), an LLM judges which single one is most worth posting — or
    that none of them are. A pick is permanently marked used, so posts never
    repeat even though the same underlying pool is re-queried every run.

    When `renderer` is set, the explainer video is rendered best-effort: any
    failure logs and the draft queues with an empty video_url (Make.com posts
    text-only for those) — a missing animation never blocks a good post.

    The finished draft is also saved to social_queue and, when `queue` is set,
    pushed to it (the Make.com webhook feeding the approval Sheet). A push
    failure is raised only after the draft and its queue row are committed —
    the run shows red, but `social-approve` can re-send the stored row."""
    candidate_ids = repository.list_viral_candidates(conn)
    if not candidate_ids:
        logger.info("No social-draft candidates (need >5 reports, not yet used)")
        return SocialDraftResult()

    candidates = []
    for opportunity_id in candidate_ids:
        opportunity = repository.load_opportunity(conn, opportunity_id)
        brief = repository.load_brief(conn, opportunity_id)
        assert brief is not None  # inner join in list_viral_candidates guarantees this
        candidates.append((opportunity, brief))

    pick = llm.pick_viral_opportunity(candidates)
    if pick.opportunity_id is None:
        logger.info("Viral pick judged none of %d candidates worth posting this run", len(candidates))
        return SocialDraftResult()

    opportunity, brief = next((o, b) for o, b in candidates if o.id == pick.opportunity_id)
    copy = llm.write_social_draft(opportunity, brief)
    date = now.date().isoformat()

    video_url = ""
    if renderer is not None:
        try:
            video_url = renderer.render(build_scene_script(date, opportunity, copy), opportunity.id)
        except Exception:
            logger.exception(
                "Video render failed for %s; queueing the draft without a video", opportunity.id
            )

    section = format_social_draft(date, opportunity, copy, video_url)
    prepend_social_draft(draft_path, section)
    repository.mark_social_posted(conn, opportunity.id, now)

    entry = SocialQueueEntry(
        opportunity_id=opportunity.id,
        date=date,
        linkedin_post=compose_linkedin_post(copy),
        x_thread=compose_x_thread(opportunity, copy),
        link=evidence_link(opportunity),
        video_url=video_url,
    )
    repository.save_social_queue_entry(conn, entry)
    conn.commit()
    logger.info("Social draft written for %r (%d reports, %d people)", opportunity.title[:70], opportunity.frequency, opportunity.distinct_authors)

    if queue is not None:
        # Draft + queue row are already committed: if this raises, the run
        # fails loudly but nothing is lost — social-approve re-sends the row.
        queue.push(entry)
        repository.mark_social_queued(conn, opportunity.id, now)
        conn.commit()
        logger.info("Social draft queued to the approval sheet for %s", opportunity.id)

    return SocialDraftResult(opportunity_id=opportunity.id)


def run_recluster(
    llm: LLMSearchPort,
    tracker: TrackerPort,
    conn: sqlite3.Connection,
    now: datetime,
) -> ReclusterResult:
    """One-time maintenance: rebuild the clustering layer under the current
    match criterion. Closes every tracked GitHub issue (their opportunities are
    about to be replaced), wipes Opportunities/briefs/issue links — Pain Points
    and their classifications are kept, nothing is re-classified — then replays
    every stored Pain Point summary through the matcher in arrival order.
    Solvability marks start NULL, so the next ingestion's phase 3 rebuilds
    briefs and issues under the current gates. Run via the manual
    "Recluster" workflow whenever the criterion changes enough to warrant it.
    """
    result = ReclusterResult()

    for opportunity_id in repository.list_tracked_opportunity_ids(conn):
        issue = repository.load_issue(conn, opportunity_id)
        assert issue is not None
        if issue.status == "open":
            tracker.close_issue(
                issue.issue_number,
                "Closing automatically: the clustering criterion changed and all Opportunities "
                "are being re-derived. A recurring problem will resurface as a new issue once it "
                "crosses the current gate.",
            )
            result.issues_closed += 1
    logger.info("Closed %d tracked issues", result.issues_closed)

    repository.delete_derived_clustering_state(conn)
    conn.commit()

    summaries = repository.list_pain_point_summaries(conn)
    logger.info("Reclustering %d pain points under the current match criterion", len(summaries))
    for pain_point_id, summary in summaries:
        _, created = _assign_to_opportunity(llm, conn, summary, pain_point_id, now)
        if created:
            result.opportunities_created += 1
        result.pain_points_reclustered += 1
        if result.pain_points_reclustered % CLASSIFY_BATCH_SIZE == 0:
            conn.commit()
            logger.info(
                "Recluster progress: %d/%d pain points into %d opportunities (committed)",
                result.pain_points_reclustered,
                len(summaries),
                result.opportunities_created,
            )

    conn.commit()
    logger.info(
        "Recluster done: %d pain points into %d opportunities; run ingest to rebuild briefs/issues",
        result.pain_points_reclustered,
        result.opportunities_created,
    )
    return result
