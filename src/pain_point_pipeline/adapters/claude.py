"""Real LLMSearchPort adapter (ADR-0003 refinement): Claude via the Anthropic API.

Structured judgments (classify, cluster, judge solvable, write brief, estimate
effort) are inherited from StructuredJudgmentAdapter (_structured_llm.py) —
this module only adds Claude-flavored client construction and the one method
that's genuinely Claude-specific: check_competitors, which uses Claude's
server-side web_search tool. Not the default LLM provider as of 2026-07-12
(see adapters/deepseek.py) — set LLM_PROVIDER=claude to use this instead.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

from pain_point_pipeline.adapters._structured_llm import LLMResponseError, StructuredJudgmentAdapter

__all__ = ["ClaudeLLMSearchAdapter", "LLMResponseError", "DEFAULT_MODEL", "model_from_env"]

# Haiku: the cheapest Claude tier (~5x cheaper than Opus), and plenty for short
# classification/clustering judgments at this pipeline's volume. Set the
# CLAUDE_MODEL env var (e.g. "claude-sonnet-5" or "claude-opus-4-8") to trade
# money for judgment quality.
DEFAULT_MODEL = "claude-haiku-4-5"
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

_COMPETITOR_SYSTEM = """\
Given a problem summary, use web search to check whether existing tools already \
solve this problem. Answer in one short, plain sentence, under 30 words, \
simple enough for a 10-year-old to follow: name a tool if you find one, and \
say whether this still looks like a real gap."""


def _web_search_tool_type(model: str) -> str:
    if model.startswith(_DYNAMIC_SEARCH_MODEL_PREFIXES):
        return "web_search_20260209"
    return "web_search_20250305"


def model_from_env() -> str:
    # `or`, not a .get() default: GitHub Actions passes undefined repository
    # variables as *empty strings*, which must fall back to the default too.
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL


class ClaudeLLMSearchAdapter(StructuredJudgmentAdapter):
    """LLMSearchPort implementation backed by the Anthropic API."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None) -> None:
        self._client = client or anthropic.Anthropic()
        self._model = model or model_from_env()

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
