"""Real LLMSearchPort adapter (ADR-0003 refinement): Claude via the Anthropic API.

Implements the six domain-specific methods on ports.LLMSearchPort by calling
Claude with structured (`messages.parse`) requests for judgments, and the
server-side web_search tool for the competitor check. Swappable per ADR-0003:
nothing outside this module knows it's Claude.
"""

from __future__ import annotations

import os
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel

from pain_point_pipeline.models import EffortSize, OpportunitySummary, PainPoint, RawItem
from pain_point_pipeline.ports import (
    BriefNarrative,
    ClusterMatch,
    EffortEstimate,
    PainPointClassification,
    SolvabilityJudgement,
)

DEFAULT_MODEL = "claude-opus-4-8"
_JUDGMENT_MAX_TOKENS = 1024
_SEARCH_MAX_TOKENS = 2048


class _PainPointClassificationModel(BaseModel):
    is_pain_point: bool
    summary: str


class _ClusterMatchModel(BaseModel):
    matched_opportunity_id: str | None


class _SolvabilityJudgementModel(BaseModel):
    solvable: bool
    rationale: str


class _BriefNarrativeModel(BaseModel):
    problem_summary: str
    solution_sketch: str


class _EffortEstimateModel(BaseModel):
    size: Literal["S", "M", "L", "XL"]
    rationale: str


_CLASSIFY_SYSTEM = """\
You classify posts and comments from the Roblox/game-dev developer community \
(Reddit and the Roblox DevForum). A Pain Point is a post or comment that expresses \
genuine frustration, an unmet need, or a workaround for a problem. Generic venting \
with no underlying unmet need does not qualify. Judge the given text and, if it is \
a Pain Point, write a one-sentence plain-language summary of the underlying problem."""

_MATCH_SYSTEM = """\
You cluster Pain Points from the Roblox/game-dev community into Opportunities — \
groups of Pain Points that describe the same recurring underlying problem. Given a \
new Pain Point and a list of existing Opportunity candidates (id and title), decide \
whether the new Pain Point matches one of them. Return that candidate's exact id if \
it matches, or null if this is a novel problem that should start a new Opportunity. \
Only match when the underlying problem is genuinely the same, not merely similar."""

_SOLVABLE_SYSTEM = """\
An Opportunity is Solvable if a solo software developer could plausibly build a \
piece of software addressing the underlying problem — as opposed to problems only \
the platform owner (Roblox Corporation) could fix, such as engine bugs, Studio \
crashes, or platform policy issues. Judge solvability for the given Pain Points and \
give a one-sentence rationale."""

_BRIEF_SYSTEM = """\
Write the narrative core of an Opportunity Brief for a recurring problem shared by \
multiple Roblox/game-dev community members, based on their Pain Points. Give a \
plain-language problem summary, and a rough, non-binding sketch of what a tool \
addressing this problem might look like."""

_EFFORT_SYSTEM = """\
Estimate the effort required for a solo software engineer — an experienced \
generalist, not a novice — to build the described solution sketch. Use a t-shirt \
size (S, M, L, or XL) with a one-line rationale for what makes it that size. Avoid \
false-precision hour estimates."""

_COMPETITOR_SYSTEM = """\
Given a problem summary, use web search to check whether existing tools already \
solve this problem. Summarize what you find in 2-4 sentences: name any existing \
tools or products, their rough positioning, and whether the problem still looks \
underserved."""


def _pain_points_block(pain_points: list[PainPoint]) -> str:
    return "\n".join(f"- {pp.summary} (source: {pp.raw_item.url})" for pp in pain_points)


class LLMRefusalError(RuntimeError):
    """Raised when Claude declines a request or a structured response fails to parse."""


_T = TypeVar("_T", bound=BaseModel)


def _require_parsed(response: Any, parsed: _T | None) -> _T:
    if parsed is None:
        raise LLMRefusalError(f"Claude did not return a parseable response (stop_reason={response.stop_reason!r})")
    return parsed


class ClaudeLLMSearchAdapter:
    """LLMSearchPort implementation backed by the Anthropic API."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = DEFAULT_MODEL) -> None:
        self._client = client or anthropic.Anthropic()
        self._model = model

    def classify_pain_point(self, item: RawItem) -> PainPointClassification:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=_CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": item.text}],
            output_format=_PainPointClassificationModel,
        )
        parsed = _require_parsed(response, response.parsed_output)
        return PainPointClassification(is_pain_point=parsed.is_pain_point, summary=parsed.summary)

    def match_or_create_opportunity(
        self, item: RawItem, candidates: list[OpportunitySummary]
    ) -> ClusterMatch:
        if not candidates:
            return ClusterMatch(opportunity_id=None)

        candidate_block = "\n".join(f"- id={c.id}: {c.title}" for c in candidates)
        prompt = f"New Pain Point text:\n{item.text}\n\nExisting Opportunity candidates:\n{candidate_block}"
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=_MATCH_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_ClusterMatchModel,
        )
        matched_id = _require_parsed(response, response.parsed_output).matched_opportunity_id
        # Defend against a hallucinated id that isn't one of the candidates offered.
        valid_ids = {c.id for c in candidates}
        if matched_id not in valid_ids:
            matched_id = None
        return ClusterMatch(opportunity_id=matched_id)

    def judge_solvable(self, pain_points: list[PainPoint]) -> SolvabilityJudgement:
        prompt = f"Pain Points:\n{_pain_points_block(pain_points)}"
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=_SOLVABLE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_SolvabilityJudgementModel,
        )
        parsed = _require_parsed(response, response.parsed_output)
        return SolvabilityJudgement(solvable=parsed.solvable, rationale=parsed.rationale)

    def write_brief_narrative(self, pain_points: list[PainPoint]) -> BriefNarrative:
        prompt = f"Pain Points:\n{_pain_points_block(pain_points)}"
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=_BRIEF_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_BriefNarrativeModel,
        )
        parsed = _require_parsed(response, response.parsed_output)
        return BriefNarrative(problem_summary=parsed.problem_summary, solution_sketch=parsed.solution_sketch)

    def check_competitors(self, problem_summary: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=_SEARCH_MAX_TOKENS,
            system=_COMPETITOR_SYSTEM,
            messages=[{"role": "user", "content": problem_summary}],
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        return " ".join(text_blocks).strip()

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate:
        prompt = f"Problem: {problem_summary}\n\nSolution sketch: {solution_sketch}"
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=_EFFORT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=_EffortEstimateModel,
        )
        parsed = _require_parsed(response, response.parsed_output)
        size: EffortSize = parsed.size
        return EffortEstimate(size=size, rationale=parsed.rationale)


def model_from_env() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
