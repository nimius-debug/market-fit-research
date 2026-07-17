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
    Opportunity,
    OpportunityBrief,
    OpportunitySummary,
    PainPoint,
    RawItem,
    SceneScript,
    SocialQueueEntry,
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
    user_flow: tuple[str, ...] = ()
    """2-4 short steps showing how someone would actually use the solution sketch."""


@dataclass(frozen=True)
class EffortEstimate:
    size: EffortSize
    rationale: str


@dataclass(frozen=True)
class ViralPick:
    """`opportunity_id` is None when nothing in the pool is worth a social post
    this cycle — a valid answer, not a fallback to avoid."""

    opportunity_id: str | None


@dataclass(frozen=True)
class SocialDraftCopy:
    """Persuasive text only — no links. The orchestrator appends the real
    evidence URL afterward so the LLM can never hallucinate or mangle one.

    The video_* fields are the on-screen text for the explainer animation
    (see video.build_scene_script). No numbers allowed in them — the real
    counts are injected deterministically, same rule as the links."""

    x_hook: str
    x_body: tuple[str, ...]
    x_closer: str
    linkedin_post: str
    video_hook: str
    video_problem: str
    video_loop_caption: str
    video_loop: tuple[str, ...]
    video_steps: tuple[str, ...]
    video_question: str


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

    def pick_viral_opportunity(
        self, candidates: list[tuple[Opportunity, OpportunityBrief]]
    ) -> ViralPick:
        """Given already-qualifying candidates (solvable, briefed, enough
        reports), judge which single one would make the best social post —
        or that none of them would."""
        ...

    def write_social_draft(self, opportunity: Opportunity, brief: OpportunityBrief) -> SocialDraftCopy: ...


class VideoRendererPort(Protocol):
    """Renders one draft's explainer video and publishes it somewhere Make.com
    can fetch by URL. Rendering is best-effort by design: run_social_draft
    catches any failure and queues the draft with an empty video_url — a
    missing animation must never block a good post (docs/deployment.md)."""

    def render(self, script: SceneScript, slug: str) -> str:
        """Render the scene script, publish the MP4, return its public URL.
        `slug` names the asset (the opportunity id). Raise on any failure."""
        ...


class SocialQueuePort(Protocol):
    """Hands a finished, publish-ready draft to the posting queue (the Make.com
    webhook that appends a `pending` row to the approval Sheet). Posting itself
    stays human-gated in the Sheet — this only queues (see docs/deployment.md)."""

    def push(self, entry: SocialQueueEntry) -> None:
        """Deliver the entry; raise on any non-success response."""
        ...


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
