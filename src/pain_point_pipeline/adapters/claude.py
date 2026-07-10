"""Real LLMSearchPort adapter (ADR-0003 refinement): Claude via the Anthropic API.

Implements the six domain-specific methods on ports.LLMSearchPort by calling
Claude with structured (`messages.parse`) requests for judgments, and the
server-side web_search tool for the competitor check. Swappable per ADR-0003:
nothing outside this module knows it's Claude.
"""

from __future__ import annotations

import os
from typing import Any, TypeVar

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

# Haiku: the cheapest tier (~5x cheaper than Opus), and plenty for short
# classification/clustering judgments at this pipeline's volume. Set the
# CLAUDE_MODEL env var (e.g. "claude-sonnet-5" or "claude-opus-4-8") to trade
# money for judgment quality.
DEFAULT_MODEL = "claude-haiku-4-5"
_JUDGMENT_MAX_TOKENS = 1024
_SEARCH_MAX_TOKENS = 2048
_MAX_SEARCH_CONTINUATIONS = 3

# The dynamic-filtering web search variant needs Opus 4.6+/Sonnet 4.6+; other
# models (incl. Haiku 4.5) must use the basic variant.
_DYNAMIC_SEARCH_MODEL_PREFIXES = (
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-fable-5",
)


def _web_search_tool_type(model: str) -> str:
    if model.startswith(_DYNAMIC_SEARCH_MODEL_PREFIXES):
        return "web_search_20260209"
    return "web_search_20250305"


def model_from_env() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)


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
    size: EffortSize
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


class LLMResponseError(RuntimeError):
    """Raised when Claude declines a request (refusal) or returns nothing usable."""


_T = TypeVar("_T", bound=BaseModel)


def _require_parsed(response: Any, parsed: _T | None) -> _T:
    if parsed is None:
        raise LLMResponseError(f"Claude did not return a parseable response (stop_reason={response.stop_reason!r})")
    return parsed


class ClaudeLLMSearchAdapter:
    """LLMSearchPort implementation backed by the Anthropic API."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None) -> None:
        self._client = client or anthropic.Anthropic()
        self._model = model or model_from_env()

    def _structured(self, system: str, prompt: str, response_model: type[_T]) -> _T:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=response_model,
        )
        return _require_parsed(response, response.parsed_output)

    def classify_pain_point(self, item: RawItem) -> PainPointClassification:
        parsed = self._structured(_CLASSIFY_SYSTEM, item.text, _PainPointClassificationModel)
        return PainPointClassification(is_pain_point=parsed.is_pain_point, summary=parsed.summary)

    def match_or_create_opportunity(
        self, item: RawItem, candidates: list[OpportunitySummary]
    ) -> ClusterMatch:
        if not candidates:
            return ClusterMatch(opportunity_id=None)

        candidate_block = "\n".join(f"- id={c.id}: {c.title}" for c in candidates)
        prompt = f"New Pain Point text:\n{item.text}\n\nExisting Opportunity candidates:\n{candidate_block}"
        parsed = self._structured(_MATCH_SYSTEM, prompt, _ClusterMatchModel)
        matched_id = parsed.matched_opportunity_id
        # Defend against a hallucinated id that isn't one of the candidates offered.
        valid_ids = {c.id for c in candidates}
        if matched_id not in valid_ids:
            matched_id = None
        return ClusterMatch(opportunity_id=matched_id)

    def judge_solvable(self, pain_points: list[PainPoint]) -> SolvabilityJudgement:
        prompt = f"Pain Points:\n{_pain_points_block(pain_points)}"
        parsed = self._structured(_SOLVABLE_SYSTEM, prompt, _SolvabilityJudgementModel)
        return SolvabilityJudgement(solvable=parsed.solvable, rationale=parsed.rationale)

    def write_brief_narrative(self, pain_points: list[PainPoint]) -> BriefNarrative:
        prompt = f"Pain Points:\n{_pain_points_block(pain_points)}"
        parsed = self._structured(_BRIEF_SYSTEM, prompt, _BriefNarrativeModel)
        return BriefNarrative(problem_summary=parsed.problem_summary, solution_sketch=parsed.solution_sketch)

    def check_competitors(self, problem_summary: str) -> str:
        messages: list[Any] = [{"role": "user", "content": problem_summary}]
        tools: list[Any] = [{"type": _web_search_tool_type(self._model), "name": "web_search"}]

        for _ in range(_MAX_SEARCH_CONTINUATIONS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_SEARCH_MAX_TOKENS,
                system=_COMPETITOR_SYSTEM,
                messages=messages,
                tools=tools,
            )
            if response.stop_reason == "refusal":
                raise LLMResponseError("Claude declined the competitor-check web search request")
            if response.stop_reason == "pause_turn":
                # Server-side search hit its per-turn iteration limit; resend to resume.
                messages = [*messages, {"role": "assistant", "content": response.content}]
                continue
            text_blocks = [block.text for block in response.content if block.type == "text"]
            return " ".join(text_blocks).strip()

        raise LLMResponseError(
            f"Competitor search did not complete after {_MAX_SEARCH_CONTINUATIONS} continuations"
        )

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate:
        prompt = f"Problem: {problem_summary}\n\nSolution sketch: {solution_sketch}"
        parsed = self._structured(_EFFORT_SYSTEM, prompt, _EffortEstimateModel)
        return EffortEstimate(size=parsed.size, rationale=parsed.rationale)
