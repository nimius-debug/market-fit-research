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
from datetime import datetime

import pytest

from pain_point_pipeline.adapters.github_tracker import GitHubTracker
from pain_point_pipeline.models import OpportunityBrief

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
