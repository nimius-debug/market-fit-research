"""Reads and writes domain objects (models.py) against the schema in db.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from pain_point_pipeline.models import (
    Opportunity,
    OpportunityBrief,
    OpportunityIssue,
    OpportunitySummary,
    PainPoint,
    RawItem,
)


def get_last_fetched_at(conn: sqlite3.Connection, source: str) -> datetime | None:
    row = conn.execute(
        "SELECT last_fetched_at FROM source_state WHERE source = ?", (source,)
    ).fetchone()
    if row is None or row["last_fetched_at"] is None:
        return None
    return datetime.fromisoformat(row["last_fetched_at"])


def set_last_fetched_at(conn: sqlite3.Connection, source: str, when: datetime) -> None:
    conn.execute(
        """
        INSERT INTO source_state (source, last_fetched_at) VALUES (?, ?)
        ON CONFLICT (source) DO UPDATE SET last_fetched_at = excluded.last_fetched_at
        """,
        (source, when.isoformat()),
    )


def insert_raw_item_if_new(conn: sqlite3.Connection, item: RawItem) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO raw_items (id, source, external_id, author, url, text, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (item.id, item.source, item.external_id, item.author, item.url, item.text, item.created_at.isoformat()),
    )
    return cursor.rowcount > 0


def list_unprocessed_raw_items(conn: sqlite3.Connection) -> list[RawItem]:
    """Raw items not yet classified — the backlog a resumed run picks up.

    Ordered oldest-first (rowid tiebreak preserves insertion order) so that
    clustering sees pain points in the same order they arrived.
    """
    rows = conn.execute(
        "SELECT * FROM raw_items WHERE processed_at IS NULL ORDER BY created_at, rowid"
    ).fetchall()
    return [
        RawItem(
            id=row["id"],
            source=row["source"],
            external_id=row["external_id"],
            author=row["author"],
            url=row["url"],
            text=row["text"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]


def mark_raw_item_processed(conn: sqlite3.Connection, raw_item_id: str, when: datetime) -> None:
    conn.execute(
        "UPDATE raw_items SET processed_at = ? WHERE id = ?", (when.isoformat(), raw_item_id)
    )


def insert_pain_point(conn: sqlite3.Connection, pain_point: PainPoint) -> None:
    conn.execute(
        "INSERT INTO pain_points (id, raw_item_id, summary, created_at) VALUES (?, ?, ?, ?)",
        (pain_point.id, pain_point.raw_item.id, pain_point.summary, pain_point.created_at.isoformat()),
    )


def create_opportunity(conn: sqlite3.Connection, opportunity_id: str, title: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO opportunities (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (opportunity_id, title, now.isoformat(), now.isoformat()),
    )


def add_pain_point_to_opportunity(
    conn: sqlite3.Connection, opportunity_id: str, pain_point_id: str, now: datetime
) -> None:
    conn.execute(
        "INSERT INTO opportunity_pain_points (opportunity_id, pain_point_id) VALUES (?, ?)",
        (opportunity_id, pain_point_id),
    )
    conn.execute(
        "UPDATE opportunities SET updated_at = ? WHERE id = ?", (now.isoformat(), opportunity_id)
    )


def update_opportunity_solvability(
    conn: sqlite3.Connection, opportunity_id: str, solvable: bool, rationale: str, checked_at: datetime
) -> None:
    conn.execute(
        "UPDATE opportunities SET solvable = ?, solvable_rationale = ?, solvability_checked_at = ? WHERE id = ?",
        (1 if solvable else 0, rationale, checked_at.isoformat(), opportunity_id),
    )


def opportunities_needing_refresh(conn: sqlite3.Connection) -> list[str]:
    """Opportunities whose solvability/brief is stale: never judged (including
    those stranded by a run that died mid-phase-3), or touched by a new Pain
    Point since they were last judged. Oldest-created first, so a repeatedly
    interrupted backlog still drains front-to-back."""
    rows = conn.execute(
        """
        SELECT id FROM opportunities
        WHERE solvability_checked_at IS NULL OR updated_at > solvability_checked_at
        ORDER BY created_at, rowid
        """
    ).fetchall()
    return [row["id"] for row in rows]


def _rejected_opportunity_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT opportunity_id FROM opportunity_issues WHERE status = 'rejected'"
    ).fetchall()
    return {row["opportunity_id"] for row in rows}


def list_open_opportunity_summaries(conn: sqlite3.Connection) -> list[OpportunitySummary]:
    """Non-Rejected Opportunities, as candidates for a new Pain Point to match against."""
    rejected = _rejected_opportunity_ids(conn)
    rows = conn.execute("SELECT id, title FROM opportunities").fetchall()
    return [OpportunitySummary(id=row["id"], title=row["title"]) for row in rows if row["id"] not in rejected]


def load_opportunity(conn: sqlite3.Connection, opportunity_id: str) -> Opportunity:
    row = conn.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    pain_points = load_pain_points_for_opportunity(conn, opportunity_id)
    return Opportunity(
        id=row["id"],
        title=row["title"],
        pain_points=pain_points,
        solvable=None if row["solvable"] is None else bool(row["solvable"]),
        solvable_rationale=row["solvable_rationale"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def load_pain_points_for_opportunity(conn: sqlite3.Connection, opportunity_id: str) -> list[PainPoint]:
    rows = conn.execute(
        """
        SELECT pp.id, pp.summary, pp.created_at,
               ri.id AS raw_id, ri.source, ri.external_id, ri.author, ri.url, ri.text, ri.created_at AS raw_created_at
        FROM pain_points pp
        JOIN opportunity_pain_points opp ON opp.pain_point_id = pp.id
        JOIN raw_items ri ON ri.id = pp.raw_item_id
        WHERE opp.opportunity_id = ?
        ORDER BY pp.created_at ASC
        """,
        (opportunity_id,),
    ).fetchall()
    return [
        PainPoint(
            id=row["id"],
            raw_item=RawItem(
                id=row["raw_id"],
                source=row["source"],
                external_id=row["external_id"],
                author=row["author"],
                url=row["url"],
                text=row["text"],
                created_at=datetime.fromisoformat(row["raw_created_at"]),
            ),
            summary=row["summary"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]


def save_brief(conn: sqlite3.Connection, brief: OpportunityBrief) -> None:
    conn.execute(
        """
        INSERT INTO opportunity_briefs
            (opportunity_id, problem_summary, solution_sketch, effort_size, effort_rationale, competitor_check, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (opportunity_id) DO UPDATE SET
            problem_summary = excluded.problem_summary,
            solution_sketch = excluded.solution_sketch,
            effort_size = excluded.effort_size,
            effort_rationale = excluded.effort_rationale,
            competitor_check = excluded.competitor_check,
            generated_at = excluded.generated_at
        """,
        (
            brief.opportunity_id,
            brief.problem_summary,
            brief.solution_sketch,
            brief.effort_size,
            brief.effort_rationale,
            brief.competitor_check,
            brief.generated_at.isoformat(),
        ),
    )


def load_brief(conn: sqlite3.Connection, opportunity_id: str) -> OpportunityBrief | None:
    row = conn.execute(
        "SELECT * FROM opportunity_briefs WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    if row is None:
        return None
    return OpportunityBrief(
        opportunity_id=row["opportunity_id"],
        problem_summary=row["problem_summary"],
        solution_sketch=row["solution_sketch"],
        effort_size=row["effort_size"],
        effort_rationale=row["effort_rationale"],
        competitor_check=row["competitor_check"],
        generated_at=datetime.fromisoformat(row["generated_at"]),
    )


def save_issue(conn: sqlite3.Connection, issue: OpportunityIssue) -> None:
    conn.execute(
        """
        INSERT INTO opportunity_issues (opportunity_id, issue_number, status, checked_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (opportunity_id) DO UPDATE SET
            issue_number = excluded.issue_number,
            status = excluded.status,
            checked_at = excluded.checked_at
        """,
        (issue.opportunity_id, issue.issue_number, issue.status, issue.checked_at.isoformat()),
    )


def list_tracked_opportunity_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT opportunity_id FROM opportunity_issues").fetchall()
    return [row["opportunity_id"] for row in rows]


def load_issue(conn: sqlite3.Connection, opportunity_id: str) -> OpportunityIssue | None:
    row = conn.execute(
        "SELECT * FROM opportunity_issues WHERE opportunity_id = ?", (opportunity_id,)
    ).fetchone()
    if row is None:
        return None
    return OpportunityIssue(
        opportunity_id=row["opportunity_id"],
        issue_number=row["issue_number"],
        status=row["status"],
        checked_at=datetime.fromisoformat(row["checked_at"]),
    )


def solvable_undigested_opportunity_ids(conn: sqlite3.Connection) -> list[str]:
    """Solvable, non-Rejected Opportunities that are new or updated since they were last digested
    (never digested, or touched again after their last_digested_at), ranked by frequency desc."""
    rejected = _rejected_opportunity_ids(conn)
    rows = conn.execute(
        """
        SELECT o.id, COUNT(opp.pain_point_id) AS frequency
        FROM opportunities o
        JOIN opportunity_pain_points opp ON opp.opportunity_id = o.id
        WHERE o.solvable = 1
          AND (o.last_digested_at IS NULL OR o.updated_at > o.last_digested_at)
        GROUP BY o.id
        ORDER BY frequency DESC, o.updated_at ASC
        """,
    ).fetchall()
    return [row["id"] for row in rows if row["id"] not in rejected]


def mark_digested(conn: sqlite3.Connection, opportunity_id: str, digest_date: str, when: datetime) -> None:
    conn.execute(
        "UPDATE opportunities SET last_digested_at = ? WHERE id = ?", (when.isoformat(), opportunity_id)
    )
    conn.execute(
        "INSERT OR IGNORE INTO digest_entries (opportunity_id, digest_date) VALUES (?, ?)",
        (opportunity_id, digest_date),
    )
