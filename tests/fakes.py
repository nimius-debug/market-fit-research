"""Scriptable fakes for the three ports, used to drive the orchestrator deterministically in tests."""

from __future__ import annotations

from datetime import datetime

from pain_point_pipeline.models import (
    IssueStatus,
    Opportunity,
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
    SocialDraftCopy,
    SolvabilityJudgement,
    ViralPick,
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
    - pick_viral_opportunity: picks the first candidate, unless any candidate's
      title contains "VIRAL_NONE", in which case it picks none (fixture for
      "the LLM judged nothing worth posting").
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

    def pick_viral_opportunity(
        self, candidates: list[tuple[Opportunity, OpportunityBrief]]
    ) -> ViralPick:
        if not candidates or any("VIRAL_NONE" in opportunity.title for opportunity, _ in candidates):
            return ViralPick(opportunity_id=None)
        return ViralPick(opportunity_id=candidates[0][0].id)

    def write_social_draft(self, opportunity: Opportunity, brief: OpportunityBrief) -> SocialDraftCopy:
        return SocialDraftCopy(
            x_hook=f"Hook (fixture): {opportunity.title}",
            x_body=("Body one (fixture).", "Body two (fixture)."),
            x_closer="Closer (fixture).",
            linkedin_post=f"LinkedIn post (fixture): {opportunity.title}",
        )


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
