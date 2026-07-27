"""Shared LLMSearchPort mechanics for any Anthropic Messages-API-compatible
backend (ADR-0003): the five structured-output judgments (classify, cluster,
judge solvable, write brief, estimate effort) are identical prompt/parsing
logic regardless of which model answers them, so concrete adapters (claude.py,
deepseek.py) subclass `StructuredJudgmentAdapter` and only supply `_client`/
`_model` plus their own `check_competitors` — the one method whose capability
(live web search or not) actually differs by backend.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError, field_validator

from pain_point_pipeline.models import (
    EffortSize,
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

logger = logging.getLogger(__name__)

_JUDGMENT_MAX_TOKENS = 1024
_MAX_STRUCTURED_ATTEMPTS = 2


def _coerce_json_encoded_list(value: object) -> object:
    """DeepSeek's tool-calling occasionally double-encodes a list field as a
    JSON string instead of a real array — observed live 2026-07-15, 3 of 6
    calls to SocialDraftModel.x_body. Transparently unwrap it instead of
    burning a retry (or, at ~50% observed rate, both retries) on something
    that's actually perfectly readable data."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


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
    user_flow: list[str]

    _coerce_user_flow = field_validator("user_flow", mode="before")(_coerce_json_encoded_list)


class EffortEstimateModel(BaseModel):
    size: EffortSize
    rationale: str


class ViralPickModel(BaseModel):
    # Same DeepSeek omitted-field quirk as ClusterMatchModel — default None.
    matched_opportunity_id: str | None = None


class SocialDraftModel(BaseModel):
    x_hook: str
    x_body: list[str]
    x_closer: str
    linkedin_post: str
    video_hook: str
    video_problem: str
    video_loop_caption: str
    video_loop: list[str]
    video_steps: list[str]
    video_question: str

    _coerce_x_body = field_validator("x_body", mode="before")(_coerce_json_encoded_list)
    _coerce_video_loop = field_validator("video_loop", mode="before")(_coerce_json_encoded_list)
    _coerce_video_steps = field_validator("video_steps", mode="before")(_coerce_json_encoded_list)


# Shared voice for every field a human actually reads (Digest, Issue titles/
# bodies). Centralized so tightening it once tightens classify/brief/effort/
# competitor-check together — reused by adapters/deepseek.py and
# adapters/claude.py for their competitor-check prompts too.
PLAIN_LANGUAGE_STYLE = """\
Write at a 5th-grade reading level: short sentences, everyday words a \
10-year-old knows. Say "use" not "utilize", "help" not "facilitate", "fix" \
not "resolve" or "address", "tool" not "solution" or "infrastructure". No \
jargon, no business-speak, no filler words like "leverage" or "streamline"."""

CLASSIFY_SYSTEM = f"""\
You classify posts and comments from AI/automation communities on Reddit — LLM \
apps, AI agents, and no-code/workflow automation. A Pain Point is a post or \
comment that expresses genuine frustration, an unmet need, or a workaround for \
a problem. Generic venting with no underlying unmet need does not qualify. \
Judge the given text and, if it is a Pain Point, write what's wrong in one \
short sentence, under 10 words. {PLAIN_LANGUAGE_STYLE} This becomes the \
headline people scan first — it must stand alone and be instantly clear."""

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

BRIEF_SYSTEM = f"""\
Write a very short brief for a problem shared by multiple AI/automation \
community members, based on their Pain Points. {PLAIN_LANGUAGE_STYLE} \
Problem: one sentence, under 12 words, saying what's wrong. Solution sketch: \
one sentence, under 12 words — just the core idea for a fix, not a full plan. \
User flow: 2 to 4 steps showing what someone would actually do to use that \
fix, in order. Each step under 8 words, starting with a verb (e.g. "Paste \
your API key.", "Get an alert when it breaks."). Skip steps a user wouldn't \
notice, like backend setup."""

EFFORT_SYSTEM = f"""\
Estimate the effort required for a solo software engineer — an experienced \
generalist, not a novice — to build the described solution sketch. Use a \
t-shirt size (S, M, L, or XL). {PLAIN_LANGUAGE_STYLE} One reason why, under \
12 words — no hour estimates."""

VIRAL_PICK_SYSTEM = """\
You are picking which ONE recurring AI/automation problem, from a list of \
candidates that already qualify (multiple people reported it, a solo \
developer could build a fix), would make the best social media post. Judge \
by: how many people would instantly think "that's exactly my problem", how \
sharp and specific the core tension is, and how easy the fix idea is to \
picture. Given the candidates (id, title, problem, report/people counts), \
return the id of the single best one — or null if none of them would \
genuinely make a good post. Null is a valid answer; do not pick out of \
obligation."""

SOCIAL_DRAFT_SYSTEM = f"""\
Write social media copy for a real recurring problem found in AI/automation \
communities, based on its brief. {PLAIN_LANGUAGE_STYLE} Use direct-response \
hook-writing: lead with the sharpest, most specific version of the problem — \
use the real numbers you're given, never invent any. Short, punchy \
sentences. No throat-clearing ("In today's post..." is banned). \
Problem first, then the fix.

Voice: you are a builder. The one true thing "I" built is a pipeline that \
reads the AI/automation communities on Reddit and pulls out the pain points \
people keep hitting — that pipeline is the hero. Early in the post, name it \
plainly and naturally, e.g. "My Reddit pipeline flagged this again this \
week". "I built" / "I'm building" may refer ONLY to that pipeline, never to \
the fix.

Critical honesty rules, all still binding:
- Never invent personal experience of the problem: no "my agent spent money \
while I slept", no "I shipped X and it broke", no living the pain yourself. \
- Never fabricate a quote or testimonial. You are given only a paraphrased \
brief, never anyone's real words, so you have nothing to quote — do not put \
invented lines in quotation marks or style them as a real person's exact \
words. Hook instead by naming the pain as the READER's own situation \
(second person: "Your AI agent can spend money with nothing checking it") \
or as an observed pattern with its real number ("N people across the \
AI/automation communities on Reddit hit this"). Never "N people told me", \
never a fake confession.
- The fix is NOT built. Nothing has been built except the pipeline. Frame \
the fix as a proposal in your own words ("the idea:", "what if something \
just...") — never "I built" or "I'm building" the fix. State it plainly and \
completely: a reader who never clicks must still know exactly what the fix \
is. Never tease it as a cliffhanger ("here's how", "stay tuned", a lone \
emoji pointing at a link).

Ending is optional. Some posts end on a soft, genuine question ("Would you \
build this? Or is it dumb?"); some just end on the sharpest line. Do not \
force a question onto every post, and never use the stock phrase "Would you \
actually use this?".

x_hook: the first tweet. Must stop the scroll on its own, under 20 words — \
the sharpest pain as the reader's own situation ("you"), or the pattern \
plus its sharpest real number attributed to the AI/automation communities \
on Reddit. Never a fabricated quote, never an invented first-person story.
x_body: exactly 2 tweets. The first unpacks how sharp/common the pattern is \
— what people actually say, how often — and may name your pipeline as what \
surfaced it. The second states the proposed fix idea directly, not a tease. \
Each under 25 words.
x_closer: the last tweet, under 20 words — either a soft, genuine question \
or a short line pointing to where you found it. Do not write a link or URL \
yourself, one will be appended after.
linkedin_post: one post in LinkedIn's native style — short lines separated \
by blank lines, one thought per line, no section labels. A scroll-stopping \
first line (the pain as the reader's "you" situation, or the observed \
pattern with its real number — never a fabricated quote), a light line \
naming the pipeline that surfaced it, then the proposed fix stated plainly \
(never as something already built). End on a soft question or the sharpest line — \
your choice, not every post needs one. Aim for 70 to 90 words, shorter when \
the idea still lands; never over 100. No link inside it, one will be posted \
separately as a comment.

The video_* fields are the on-screen text for a short silent animation \
that plays with the LinkedIn post: hook scene, problem scene with the \
report count animating in, then a broken-loop scene (three boxes cycling \
into a dead end), then the fix steps appearing one by one, then the \
closing question. Every scene must earn its screen time — a viewer \
who reads only the video should still walk away knowing the problem and \
the proposed fix. Hard rule: NO digits or numbers in any video field — \
the real counts are shown by the animation itself, injected by the \
pipeline. All other rules above (curator voice, no invented experience, \
proposal framing, no teases) apply to these too.
video_hook: 8 words max. The pattern, sharpest form, no numbers.
video_problem: one sentence, 12 words max, no numbers — what people \
keep saying is broken.
video_loop_caption: one sentence, 10 words max, no numbers — the cycle \
people describe for THIS problem, in its own concrete terms, never a \
generic "same loop every week" line.
video_loop: exactly 3 labels, 2 words max each — the cycle's stages in \
order: what people try, where it breaks, what they fall back to (e.g. \
"Ask AI", "It breaks", "Start over") — specific to this problem, the \
break lands on the second label.
video_steps: exactly 2 or 3 captions, 6 words max each — the proposed \
fix as concrete steps a viewer could picture (e.g. "Pick a feature you \
built.", "AI hints only when stuck.").
video_question: 6 words max — the validation question (e.g. "Worth \
building?")."""


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
        return BriefNarrative(
            problem_summary=parsed.problem_summary,
            solution_sketch=parsed.solution_sketch,
            user_flow=tuple(parsed.user_flow),
        )

    def estimate_effort(self, problem_summary: str, solution_sketch: str) -> EffortEstimate:
        prompt = f"Problem: {problem_summary}\n\nSolution sketch: {solution_sketch}"
        parsed = self._structured(EFFORT_SYSTEM, prompt, EffortEstimateModel)
        return EffortEstimate(size=parsed.size, rationale=parsed.rationale)

    def check_competitors(self, problem_summary: str) -> str:
        raise NotImplementedError

    def pick_viral_opportunity(
        self, candidates: list[tuple[Opportunity, OpportunityBrief]]
    ) -> ViralPick:
        if not candidates:
            return ViralPick(opportunity_id=None)

        candidate_block = "\n".join(
            f"- id={opportunity.id}: {opportunity.title} | {brief.problem_summary} | "
            f"{opportunity.frequency} reports from {opportunity.distinct_authors} people"
            for opportunity, brief in candidates
        )
        parsed = self._structured(VIRAL_PICK_SYSTEM, candidate_block, ViralPickModel)
        matched_id = parsed.matched_opportunity_id
        # Defend against a hallucinated id that isn't one of the candidates offered.
        valid_ids = {opportunity.id for opportunity, _ in candidates}
        if matched_id not in valid_ids:
            matched_id = None
        return ViralPick(opportunity_id=matched_id)

    def write_social_draft(self, opportunity: Opportunity, brief: OpportunityBrief) -> SocialDraftCopy:
        prompt = (
            f"Title: {opportunity.title}\n"
            f"Problem: {brief.problem_summary}\n"
            f"Fix idea: {brief.solution_sketch}\n"
            f"Reports: {opportunity.frequency} from {opportunity.distinct_authors} distinct people\n"
            f"Effort: {brief.effort_size}"
        )
        parsed = self._structured(SOCIAL_DRAFT_SYSTEM, prompt, SocialDraftModel)
        return SocialDraftCopy(
            x_hook=parsed.x_hook,
            x_body=tuple(parsed.x_body),
            x_closer=parsed.x_closer,
            linkedin_post=parsed.linkedin_post,
            video_hook=parsed.video_hook,
            video_problem=parsed.video_problem,
            video_loop_caption=parsed.video_loop_caption,
            video_loop=tuple(parsed.video_loop),
            video_steps=tuple(parsed.video_steps),
            video_question=parsed.video_question,
        )
