"""Shared LLMSearchPort mechanics for any Anthropic Messages-API-compatible
backend (ADR-0003): the five structured-output judgments (classify, cluster,
judge solvable, write brief, estimate effort) are identical prompt/parsing
logic regardless of which model answers them, so concrete adapters (claude.py,
deepseek.py) subclass `StructuredJudgmentAdapter` and only supply `_client`/
`_model` plus their own `check_competitors` — the one method whose capability
(live web search or not) actually differs by backend.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from pain_point_pipeline.models import EffortSize, OpportunitySummary, PainPoint, RawItem
from pain_point_pipeline.ports import (
    BriefNarrative,
    ClusterMatch,
    EffortEstimate,
    PainPointClassification,
    SolvabilityJudgement,
)

logger = logging.getLogger(__name__)

_JUDGMENT_MAX_TOKENS = 1024
_MAX_STRUCTURED_ATTEMPTS = 2


class PainPointClassificationModel(BaseModel):
    is_pain_point: bool
    summary: str


class ClusterMatchModel(BaseModel):
    # Defaulted, not required: when the answer is "no match", DeepSeek omits
    # the field entirely instead of sending an explicit null the way Claude
    # does (observed live 2026-07-12: tool input was {}). Omitted means None.
    matched_opportunity_id: str | None = None


class SolvabilityJudgementModel(BaseModel):
    solvable: bool
    rationale: str


class BriefNarrativeModel(BaseModel):
    problem_summary: str
    solution_sketch: str


class EffortEstimateModel(BaseModel):
    size: EffortSize
    rationale: str


CLASSIFY_SYSTEM = """\
You classify posts and comments from AI/automation communities on Reddit — LLM \
apps, AI agents, and no-code/workflow automation. A Pain Point is a post or \
comment that expresses genuine frustration, an unmet need, or a workaround for \
a problem. Generic venting with no underlying unmet need does not qualify. \
Judge the given text and, if it is a Pain Point, write a summary of the \
underlying problem in plain, simple words — as if explaining it to a \
10-year-old. One short sentence, under 15 words, no jargon. This summary \
becomes the headline people scan, so it must be short and clear on its own."""

MATCH_SYSTEM = """\
You cluster Pain Points from AI/automation communities into Opportunities — \
groups of Pain Points that one piece of software could address. Given a new \
Pain Point summary and a list of existing Opportunity candidates (id and title), \
decide whether the new Pain Point belongs with one of them. The test is: could \
one well-scoped tool plausibly address both this Pain Point and the candidate's \
problem? Surface phrasing may differ — match on the underlying need, not the \
wording. Return that candidate's exact id if one matches, or null if no single \
tool would plausibly cover this and any candidate, so it should start a new \
Opportunity."""

SOLVABLE_SYSTEM = """\
An Opportunity is Solvable if a solo software developer could plausibly build a \
piece of software addressing the underlying problem — as opposed to problems only \
a platform vendor could fix, such as a closed AI model's behavior, an LLM \
provider's outage or pricing policy, or a no-code platform's own missing feature. \
Judge solvability for the given Pain Points and give a one-sentence rationale."""

BRIEF_SYSTEM = """\
Write a short, simple brief for a recurring problem shared by multiple \
AI/automation community members, based on their Pain Points. Use plain words — \
write like you're explaining it to a 10-year-old, not a business memo. No \
jargon, no filler, no marketing language. Problem: one short sentence, under \
25 words, saying what's wrong and why it's annoying. Solution sketch: one \
short sentence, under 25 words, saying what a tool could do about it."""

EFFORT_SYSTEM = """\
Estimate the effort required for a solo software engineer — an experienced \
generalist, not a novice — to build the described solution sketch. Use a t-shirt \
size (S, M, L, or XL) with one short, plain-language reason why, under 20 \
words — simple words, no jargon, no false-precision hour estimates."""


def pain_points_block(pain_points: list[PainPoint]) -> str:
    return "\n".join(f"- {pp.summary} (source: {pp.raw_item.url})" for pp in pain_points)


class LLMResponseError(RuntimeError):
    """Raised when the model declines a request (refusal) or returns nothing usable."""


_T = TypeVar("_T", bound=BaseModel)


def _tool_name(response_model: type[BaseModel]) -> str:
    return f"submit_{response_model.__name__.lower()}"


class StructuredJudgmentAdapter:
    """Base for LLMSearchPort adapters backed by `anthropic.Anthropic` (possibly
    pointed at a different base_url/api_key for an Anthropic-API-compatible
    third party). Subclasses must set `self._client` and `self._model` in
    `__init__`, and implement `check_competitors` themselves.

    Structured output is implemented via forced tool use (a single tool built
    from the pydantic model's JSON schema, with tool_choice pinned to it) —
    not the Anthropic SDK's `messages.parse(output_format=...)` convenience
    wrapper. That wrapper relies on an Anthropic-specific server-side
    structured-output mode that DeepSeek's Anthropic-compatible endpoint does
    not implement (verified live 2026-07-12: it silently returns plain text
    instead of JSON matching the schema). Forced tool use is a much older,
    more universally-supported part of the Messages API surface, so this one
    mechanism works for both adapters. `thinking` is explicitly disabled
    because deepseek-v4-flash defaults to thinking mode, which rejects a
    forced tool_choice outright ("Thinking mode does not support this
    tool_choice") — harmless for Claude, whose models default to thinking
    off already.
    """

    _client: anthropic.Anthropic
    _model: str

    def _structured(self, system: str, prompt: str, response_model: type[_T]) -> _T:
        """One retry on a malformed response: at hundreds of unattended calls
        per run, a single bad reply (schema-violating tool input, missing tool
        call) shouldn't cost a week's run — but a *second* failure raises, since
        that points at a systematic problem worth surfacing, not noise."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_STRUCTURED_ATTEMPTS + 1):
            try:
                return self._structured_once(system, prompt, response_model)
            except (ValidationError, LLMResponseError) as error:
                last_error = error
                if attempt < _MAX_STRUCTURED_ATTEMPTS:
                    logger.warning(
                        "Malformed %s response (attempt %d/%d), retrying: %s",
                        response_model.__name__,
                        attempt,
                        _MAX_STRUCTURED_ATTEMPTS,
                        error,
                    )
        assert last_error is not None
        raise last_error

    def _structured_once(self, system: str, prompt: str, response_model: type[_T]) -> _T:
        tool_name = _tool_name(response_model)
        tool: dict[str, Any] = {
            "name": tool_name,
            "description": f"Submit the result as {response_model.__name__}.",
            "input_schema": response_model.model_json_schema(),
        }
        messages: list[Any] = [{"role": "user", "content": prompt}]
        tools: list[Any] = [tool]
        tool_choice: Any = {"type": "tool", "name": tool_name}
        thinking: Any = {"type": "disabled"}
        response = self._client.messages.create(
            model=self._model,
            max_tokens=_JUDGMENT_MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            thinking=thinking,
        )
        if response.stop_reason == "refusal":
            raise LLMResponseError("Model declined the request")
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return response_model.model_validate(block.input)
        raise LLMResponseError(
            f"Model did not return the expected tool call (stop_reason={response.stop_reason!r})"
        )

    def classify_pain_point(self, item: RawItem) -> PainPointClassification:
        parsed = self._structured(CLASSIFY_SYSTEM, item.text, PainPointClassificationModel)
        return PainPointClassification(is_pain_point=parsed.is_pain_point, summary=parsed.summary)

    def match_or_create_opportunity(
        self, summary: str, candidates: list[OpportunitySummary]
    ) -> ClusterMatch:
        if not candidates:
            return ClusterMatch(opportunity_id=None)

        candidate_block = "\n".join(f"- id={c.id}: {c.title}" for c in candidates)
        prompt = f"New Pain Point summary:\n{summary}\n\nExisting Opportunity candidates:\n{candidate_block}"
        parsed = self._structured(MATCH_SYSTEM, prompt, ClusterMatchModel)
        matched_id = parsed.matched_opportunity_id
        # Defend against a hallucinated id that isn't one of the candidates offered.
        valid_ids = {c.id for c in candidates}
        if matched_id not in valid_ids:
            matched_id = None
        return ClusterMatch(opportunity_id=matched_id)

    def judge_solvable(self, pain_points: list[PainPoint]) -> SolvabilityJudgement:
        prompt = f"Pain Points:\n{pain_points_block(pain_points)}"
        parsed = self._structured(SOLVABLE_SYSTEM, prompt, SolvabilityJudgementModel)
        return SolvabilityJudgement(solvable=parsed.solvable, rationale=parsed.rationale)

    def write_brief_narrative(self, pain_points: list[PainPoint]) -> BriefNarrative:
        prompt = f"Pain Points:\n{pain_points_block(pain_points)}"
        parsed = self._structured(BRIEF_SYSTEM, prompt, BriefNarrativeModel)
        return BriefNarrative(problem_summary=parsed.problem_summary, solution_sketch=parsed.solution_sketch)

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate:
        prompt = f"Problem: {problem_summary}\n\nSolution sketch: {solution_sketch}"
        parsed = self._structured(EFFORT_SYSTEM, prompt, EffortEstimateModel)
        return EffortEstimate(size=parsed.size, rationale=parsed.rationale)

    def check_competitors(self, problem_summary: str) -> str:
        raise NotImplementedError
