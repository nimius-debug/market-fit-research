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
    updated_at TEXT NOT NULL
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
    generated_at TEXT NOT NULL
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


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
