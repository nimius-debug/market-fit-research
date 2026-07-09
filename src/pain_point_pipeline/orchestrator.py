"""Wires Source -> classify -> cluster -> solvability -> brief -> issue, and separately, Digest build.

Two entry points mirror the two GitHub Actions schedules (see docs/specs/0001-v1-pipeline.md):
`run_ingestion_batch` daily, `run_digest_build` weekly. Both operate purely through the
SourcePort/LLMSearchPort/TrackerPort seams so tests can drive them with fakes.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from pain_point_pipeline import repository
from pain_point_pipeline.digest import MAX_OPPORTUNITIES_PER_DIGEST, append_digest, format_digest_section
from pain_point_pipeline.models import OpportunityBrief, OpportunityIssue, PainPoint
from pain_point_pipeline.ports import LLMSearchPort, SourcePort, TrackerPort


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


def run_ingestion_batch(
    sources: list[SourcePort],
    llm: LLMSearchPort,
    tracker: TrackerPort,
    conn: sqlite3.Connection,
    now: datetime,
) -> IngestionResult:
    result = IngestionResult()

    for source in sources:
        since = repository.get_last_fetched_at(conn, source.name)
        for item in source.fetch_new(since):
            if not repository.insert_raw_item_if_new(conn, item):
                continue
            result.new_raw_items += 1

            classification = llm.classify_pain_point(item)
            if not classification.is_pain_point:
                continue

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

        repository.set_last_fetched_at(conn, source.name, now)

    for opportunity_id in result.touched_opportunity_ids:
        _refresh_solvability_and_brief(opportunity_id, llm, tracker, conn, now, result)

    _refresh_all_issue_statuses(tracker=tracker, conn=conn, now=now)

    conn.commit()
    return result


def _refresh_solvability_and_brief(
    opportunity_id: str,
    llm: LLMSearchPort,
    tracker: TrackerPort,
    conn: sqlite3.Connection,
    now: datetime,
    result: IngestionResult,
) -> None:
    opportunity = repository.load_opportunity(conn, opportunity_id)
    judgement = llm.judge_solvable(opportunity.pain_points)
    repository.update_opportunity_solvability(conn, opportunity_id, judgement.solvable, judgement.rationale)

    if not judgement.solvable:
        return

    narrative = llm.write_brief_narrative(opportunity.pain_points)
    competitor_check = llm.check_competitors(narrative.problem_summary)
    effort = llm.estimate_effort(narrative.problem_summary, narrative.solution_sketch)
    brief = OpportunityBrief(
        opportunity_id=opportunity_id,
        problem_summary=narrative.problem_summary,
        solution_sketch=narrative.solution_sketch,
        effort_size=effort.size,
        effort_rationale=effort.rationale,
        competitor_check=competitor_check,
        generated_at=now,
    )
    repository.save_brief(conn, brief)

    if repository.load_issue(conn, opportunity_id) is None:
        issue_number = tracker.create_issue(opportunity_id, brief, title=opportunity.title)
        repository.save_issue(
            conn, OpportunityIssue(opportunity_id=opportunity_id, issue_number=issue_number, status="open", checked_at=now)
        )
        result.issues_created[opportunity_id] = issue_number


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
