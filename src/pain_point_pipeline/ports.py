"""The three seams real adapters plug into (ADR-0003): Source, LLMSearch, and Tracker.

Tests drive the orchestrator against fakes of all three; tickets 2-5 add real
adapters behind these same interfaces without changing orchestrator code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pain_point_pipeline.models import (
    EffortSize,
    IssueStatus,
    OpportunityBrief,
    OpportunitySummary,
    PainPoint,
    RawItem,
)


@dataclass(frozen=True)
class PainPointClassification:
    is_pain_point: bool
    summary: str


@dataclass(frozen=True)
class ClusterMatch:
    """`opportunity_id` is None when the item should start a new Opportunity."""

    opportunity_id: str | None


@dataclass(frozen=True)
class SolvabilityJudgement:
    solvable: bool
    rationale: str


@dataclass(frozen=True)
class BriefNarrative:
    """The prose core of an Opportunity Brief; the orchestrator adds competitor check and effort estimate."""

    problem_summary: str
    solution_sketch: str


@dataclass(frozen=True)
class EffortEstimate:
    size: EffortSize
    rationale: str


class SourcePort(Protocol):
    """Reads new posts/comments from one community platform (Reddit, DevForum, ...)."""

    name: str

    def fetch_new(self, since: datetime | None) -> list[RawItem]:
        """Return items created after `since`, or all available items if `since` is None."""
        ...


class LLMSearchPort(Protocol):
    """All LLM/web-search judgment calls the pipeline makes, behind one swappable adapter."""

    def classify_pain_point(self, item: RawItem) -> PainPointClassification: ...

    def match_or_create_opportunity(
        self, summary: str, candidates: list[OpportunitySummary]
    ) -> ClusterMatch:
        """Match a Pain Point's one-sentence summary (not raw post text: the
        candidates are titles in the same register, so compare like with like)
        against the candidate Opportunities."""
        ...

    def judge_solvable(self, pain_points: list[PainPoint]) -> SolvabilityJudgement: ...

    def write_brief_narrative(self, pain_points: list[PainPoint]) -> BriefNarrative: ...

    def check_competitors(self, problem_summary: str) -> str: ...

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate: ...


class TrackerPort(Protocol):
    """Surfaces an Opportunity for human review and reports back its Rejected status."""

    def create_issue(self, opportunity_id: str, brief: OpportunityBrief, title: str) -> int:
        """Returns the created issue number."""
        ...

    def get_status(self, issue_number: int) -> IssueStatus: ...

    def update_issue_title(self, issue_number: int, title: str) -> None:
        """Keep the (N reports, M people) counts in the title current as an
        Opportunity accretes Pain Points."""
        ...

    def close_issue(self, issue_number: int, comment: str) -> None:
        """Close an issue that no longer tracks a live Opportunity (recluster cleanup)."""
        ...
