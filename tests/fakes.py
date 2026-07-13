"""Scriptable fakes for the three ports, used to drive the orchestrator deterministically in tests."""

from __future__ import annotations

from datetime import datetime

from pain_point_pipeline.models import (
    IssueStatus,
    OpportunityBrief,
    OpportunitySummary,
    PainPoint,
    RawItem,
)
from pain_point_pipeline.ports import (
    BriefNarrative,
    ClusterMatch,
    EffortEstimate,
    PainPointClassification,
    SolvabilityJudgement,
)


class FakeSource:
    """Returns a fixed, scripted list of items regardless of `since`."""

    def __init__(self, name: str, items: list[RawItem]) -> None:
        self.name = name
        self._items = items

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        if since is None:
            return list(self._items)
        return [item for item in self._items if item.created_at > since]


class FakeLLMSearch:
    """Deterministic rules instead of real model calls, keyed by substrings in the item text.

    - Text containing "PAINPOINT" classifies as a Pain Point; everything else does not.
    - Text containing "CLUSTER_WITH:<title>" matches the existing candidate with that title.
    - Text containing "UNSOLVABLE" makes the owning Opportunity judged not Solvable.
    """

    def classify_pain_point(self, item: RawItem) -> PainPointClassification:
        is_pain_point = "PAINPOINT" in item.text
        return PainPointClassification(
            is_pain_point=is_pain_point,
            summary=item.text.splitlines()[0][:80] if is_pain_point else "",
        )

    def match_or_create_opportunity(
        self, summary: str, candidates: list[OpportunitySummary]
    ) -> ClusterMatch:
        for candidate in candidates:
            if f"CLUSTER_WITH:{candidate.title}" in summary:
                return ClusterMatch(opportunity_id=candidate.id)
        return ClusterMatch(opportunity_id=None)

    def judge_solvable(self, pain_points: list[PainPoint]) -> SolvabilityJudgement:
        if any("UNSOLVABLE" in pp.raw_item.text for pp in pain_points):
            return SolvabilityJudgement(solvable=False, rationale="Fixture-marked unsolvable.")
        return SolvabilityJudgement(solvable=True, rationale="Fixture-marked solvable by a solo dev.")

    def write_brief_narrative(self, pain_points: list[PainPoint]) -> BriefNarrative:
        return BriefNarrative(
            problem_summary=f"Recurring problem: {pain_points[0].summary}",
            solution_sketch="A small tool addressing the above.",
            user_flow=("Open the tool (fixture).", "See the fix (fixture)."),
        )

    def check_competitors(self, problem_summary: str) -> str:
        return "No direct competitors found (fixture)."

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate:
        return EffortEstimate(size="S", rationale="Small, well-scoped tool (fixture).")


class FakeTracker:
    """In-memory GitHub Issues stand-in. Tests can force a status via `set_status`."""

    def __init__(self) -> None:
        self._next_issue_number = 1
        self._issues: dict[int, IssueStatus] = {}
        self.created: dict[str, int] = {}
        self.titles: dict[int, str] = {}
        self.closed: list[int] = []

    def create_issue(self, opportunity_id: str, brief: OpportunityBrief, title: str) -> int:
        issue_number = self._next_issue_number
        self._next_issue_number += 1
        self._issues[issue_number] = "open"
        self.created[opportunity_id] = issue_number
        self.titles[issue_number] = title
        return issue_number

    def get_status(self, issue_number: int) -> IssueStatus:
        return self._issues[issue_number]

    def set_status(self, issue_number: int, status: IssueStatus) -> None:
        self._issues[issue_number] = status

    def update_issue_title(self, issue_number: int, title: str) -> None:
        self.titles[issue_number] = title

    def close_issue(self, issue_number: int, comment: str) -> None:
        self._issues[issue_number] = "rejected"
        self.closed.append(issue_number)
