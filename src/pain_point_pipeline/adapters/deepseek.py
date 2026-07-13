"""Real LLMSearchPort adapter: DeepSeek via its Anthropic-API-compatible endpoint.

**Default LLM provider as of 2026-07-12** (see cli._build_llm) — deepseek-v4-flash
is far cheaper than even Claude Haiku at this pipeline's classification/clustering
volume. Reuses the `anthropic` SDK pointed at DeepSeek's Anthropic-compatible
base_url (https://api.deepseek.com/anthropic), which DeepSeek's own docs describe
as accepting the same request/response shape as Claude's Messages API. Set
LLM_PROVIDER=claude (see docs/deployment.md) to fall back to Claude instead.

check_competitors has no live web search: unlike Claude, DeepSeek doesn't expose
a server-side hosted search tool through this endpoint (that's Anthropic
infrastructure, not part of the Messages API surface DeepSeek mirrors). This
adapter's check_competitors instead asks the model to judge from its own
training knowledge, which the prompt makes it disclose explicitly — a
knowingly weaker signal than Claude's live search, chosen for cost.
"""

from __future__ import annotations

import os

import anthropic
from pydantic import BaseModel

from pain_point_pipeline.adapters._structured_llm import StructuredJudgmentAdapter

__all__ = ["DeepSeekLLMSearchAdapter", "DEFAULT_MODEL", "model_from_env"]

DEFAULT_MODEL = "deepseek-v4-flash"
_BASE_URL = "https://api.deepseek.com/anthropic"

_COMPETITOR_SYSTEM = """\
Given a problem summary, judge from your own training knowledge (no live web \
search — say so if you're not sure) whether tools like this already exist. \
Answer in one short, plain sentence, under 25 words, simple enough for a \
10-year-old to follow: name a tool if you know one, and say whether this \
still looks like a real gap."""


class _CompetitorCheckModel(BaseModel):
    summary: str


def model_from_env() -> str:
    # `or`, not a .get() default: GitHub Actions passes undefined repository
    # variables as *empty strings*, which must fall back to the default too —
    # a live run once sent model="" and got a 400 back from the API.
    return os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL


def _build_client(client: anthropic.Anthropic | None, api_key: str | None) -> anthropic.Anthropic:
    if client is not None:
        return client
    key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY must be set (a DeepSeek platform API key)")
    return anthropic.Anthropic(api_key=key, base_url=_BASE_URL)


class DeepSeekLLMSearchAdapter(StructuredJudgmentAdapter):
    """LLMSearchPort implementation backed by DeepSeek's Anthropic-compatible API."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = _build_client(client, api_key)
        self._model = model or model_from_env()

    def check_competitors(self, problem_summary: str) -> str:
        parsed = self._structured(_COMPETITOR_SYSTEM, problem_summary, _CompetitorCheckModel)
        return parsed.summary
