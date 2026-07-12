"""Real TrackerPort adapter: GitHub Issues via the GitHub REST API (PyGithub).

Each new Opportunity gets a GitHub Issue; Rejected status (CONTEXT.md) is read
from that Issue's state — closed, or labeled "rejected" — at Digest-build time.
Uses the REST API rather than shelling out to `gh`, since GITHUB_TOKEN is
already available as a GitHub Actions secret in the runner (see ADR-0002).
"""

from __future__ import annotations

import os

from github import Github
from github.Repository import Repository

from pain_point_pipeline.models import IssueStatus, OpportunityBrief

DEFAULT_REPO = "nimius-debug/market-fit-research"
REJECTED_LABEL = "rejected"


def _format_issue_body(opportunity_id: str, brief: OpportunityBrief) -> str:
    return (
        f"**Problem:** {brief.problem_summary}\n\n"
        f"**Solution sketch:** {brief.solution_sketch}\n\n"
        f"**Competitor check:** {brief.competitor_check}\n\n"
        f"**Effort estimate:** {brief.effort_size} — {brief.effort_rationale}\n\n"
        f"Close this issue, or label it `{REJECTED_LABEL}`, to reject this Opportunity "
        "and suppress it from future Digests.\n\n"
        f"<!-- opportunity_id: {opportunity_id} -->"
    )


class GitHubTracker:
    """TrackerPort implementation backed by GitHub Issues."""

    def __init__(self, token: str | None = None, repo_full_name: str | None = None) -> None:
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN must be set to create or read Issues")
        self._repo_full_name = repo_full_name or os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
        self._client = Github(token)
        self._repo: Repository | None = None

    def _get_repo(self) -> Repository:
        if self._repo is None:
            self._repo = self._client.get_repo(self._repo_full_name)
        return self._repo

    def create_issue(self, opportunity_id: str, brief: OpportunityBrief, title: str) -> int:
        issue = self._get_repo().create_issue(title=title, body=_format_issue_body(opportunity_id, brief))
        return issue.number

    def get_status(self, issue_number: int) -> IssueStatus:
        issue = self._get_repo().get_issue(issue_number)
        if issue.state == "closed":
            return "rejected"
        if any(label.name == REJECTED_LABEL for label in issue.labels):
            return "rejected"
        return "open"

    def update_issue_title(self, issue_number: int, title: str) -> None:
        self._get_repo().get_issue(issue_number).edit(title=title)

    def close_issue(self, issue_number: int, comment: str) -> None:
        issue = self._get_repo().get_issue(issue_number)
        issue.create_comment(comment)
        issue.edit(state="closed", state_reason="not_planned")
