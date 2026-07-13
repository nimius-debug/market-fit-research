"""SQLite storage for the pipeline's domain records.

Kept deliberately thin: plain SQL over a connection, no ORM. The schema mirrors
the domain model in models.py (see CONTEXT.md for the terms).
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    author TEXT NOT NULL,
    url TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    processed_at TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS pain_points (
    id TEXT PRIMARY KEY,
    raw_item_id TEXT NOT NULL REFERENCES raw_items(id),
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    solvable INTEGER,
    solvable_rationale TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_digested_at TEXT,
    solvability_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS opportunity_pain_points (
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    pain_point_id TEXT NOT NULL REFERENCES pain_points(id),
    PRIMARY KEY (opportunity_id, pain_point_id)
);

CREATE TABLE IF NOT EXISTS opportunity_briefs (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id),
    problem_summary TEXT NOT NULL,
    solution_sketch TEXT NOT NULL,
    effort_size TEXT NOT NULL,
    effort_rationale TEXT NOT NULL,
    competitor_check TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    user_flow TEXT
);

CREATE TABLE IF NOT EXISTS opportunity_issues (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id),
    issue_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_entries (
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    digest_date TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, digest_date)
);

CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    last_fetched_at TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """In-place migrations for databases created before a schema change.

    processed_at (2026-07): rows written before the column existed were all
    classified inline in the same run that inserted them (the pre-resumable
    orchestrator committed all-or-nothing), so they are backfilled as processed
    rather than left NULL — otherwise the first run after this migration would
    re-classify the entire historical backlog.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(raw_items)")}
    if "processed_at" not in columns:
        conn.execute("ALTER TABLE raw_items ADD COLUMN processed_at TEXT")
        conn.execute("UPDATE raw_items SET processed_at = created_at")
        conn.commit()

    # solvability_checked_at (2026-07): opportunities judged before the column
    # existed have solvable set (0 or 1); backfill them as checked so the first
    # run after this migration only refreshes the genuinely unjudged backlog —
    # the ones a timed-out phase 3 stranded with solvable still NULL.
    opportunity_columns = {row["name"] for row in conn.execute("PRAGMA table_info(opportunities)")}
    if "solvability_checked_at" not in opportunity_columns:
        conn.execute("ALTER TABLE opportunities ADD COLUMN solvability_checked_at TEXT")
        conn.execute("UPDATE opportunities SET solvability_checked_at = updated_at WHERE solvable IS NOT NULL")
        conn.commit()

    # user_flow (2026-07): briefs written before this column existed just have
    # no flow steps (NULL) — repository.load_brief treats that as an empty
    # tuple, so the Digest/Issue simply omit the section rather than error.
    brief_columns = {row["name"] for row in conn.execute("PRAGMA table_info(opportunity_briefs)")}
    if "user_flow" not in brief_columns:
        conn.execute("ALTER TABLE opportunity_briefs ADD COLUMN user_flow TEXT")
        conn.commit()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
