"""Live-API contract test for GitHubTracker (ticket 5).

Excluded from the default test run. Run explicitly with:

    pytest -m integration tests/test_github_tracker_integration.py

Requires GITHUB_TOKEN (repo-scope) in the environment. Creates and closes a
real issue in the target repo (default: nimius-debug/market-fit-research, or
GITHUB_REPOSITORY if set) to verify create_issue/get_status round-trip.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

import pytest
from fakes import FakeLLMSearch, FakeSource

from pain_point_pipeline.adapters.github_tracker import GitHubTracker
from pain_point_pipeline.models import OpportunityBrief
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="requires GITHUB_TOKEN"),
]


def test_create_issue_then_close_flips_status_to_rejected() -> None:
    tracker = GitHubTracker()
    brief = OpportunityBrief(
        opportunity_id=str(uuid.uuid4()),
        problem_summary="[integration test] placeholder problem summary.",
        solution_sketch="[integration test] placeholder solution sketch.",
        effort_size="S",
        effort_rationale="[integration test] placeholder rationale.",
        competitor_check="[integration test] placeholder competitor check.",
        generated_at=datetime(2026, 7, 9, 12, 0, 0),
    )

    issue_number = tracker.create_issue(str(uuid.uuid4()), brief, title="[integration test] contract test issue")
    try:
        assert tracker.get_status(issue_number) == "open"

        repo = tracker._get_repo()  # noqa: SLF001 - test-only, to close the issue we just created
        repo.get_issue(issue_number).edit(state="closed")

        assert tracker.get_status(issue_number) == "rejected"
    finally:
        repo = tracker._get_repo()  # noqa: SLF001
        issue = repo.get_issue(issue_number)
        if issue.state != "closed":
            issue.edit(state="closed")


def test_pipeline_creates_real_issue_and_suppresses_after_rejection(conn, now, make_item, digest_path) -> None:
    """Proves the orchestrator seam picks up a real Rejected status, not just get_status() in isolation."""
    tracker = GitHubTracker()
    llm = FakeLLMSearch()
    item = make_item("PAINPOINT [integration test] scripting is painful")
    source = FakeSource("reddit", [item])

    ingest_result = run_ingestion_batch([source], llm, tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    issue_number = ingest_result.issues_created[opportunity_id]

    try:
        first_digest = run_digest_build(conn, tracker, digest_path, now)
        assert first_digest.included_opportunity_ids == [opportunity_id]

        repo = tracker._get_repo()  # noqa: SLF001 - test-only, to reject the issue we just created
        repo.get_issue(issue_number).edit(state="closed")

        second_digest = run_digest_build(conn, tracker, digest_path, now + timedelta(days=7))
        assert second_digest.included_opportunity_ids == []
    finally:
        repo = tracker._get_repo()  # noqa: SLF001
        issue = repo.get_issue(issue_number)
        if issue.state != "closed":
            issue.edit(state="closed")
