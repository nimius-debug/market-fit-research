"""Formats and prepends the weekly Digest file (CONTEXT.md: Digest).

Entries are written in plain, simple words on purpose (see the brief/effort/
competitor-check prompts in adapters/_structured_llm.py and adapters/deepseek.py) —
this is the file meant to be read quickly, not a design doc.
"""

from __future__ import annotations

import os

from pain_point_pipeline.models import Opportunity, OpportunityBrief

MAX_OPPORTUNITIES_PER_DIGEST = 5
_TITLE = "# Digest\n\n"
_MAX_EVIDENCE_LINKS = 2


def format_opportunity_entry(opportunity: Opportunity, brief: OpportunityBrief) -> str:
    lines = [
        f"### {opportunity.title}",
        "",
        f"**{opportunity.frequency} reports from {opportunity.distinct_authors} people**",
        "",
        f"**Problem:** {brief.problem_summary}",
        "",
        f"**Fix idea:** {brief.solution_sketch}",
        "",
        f"**Effort:** {brief.effort_size} — {brief.effort_rationale}",
        "",
        f"**Already out there?** {brief.competitor_check}",
    ]
    if brief.user_flow:
        lines.append("")
        lines.append("**How it would work:**")
        for n, step in enumerate(brief.user_flow, start=1):
            lines.append(f"{n}. {step}")
    lines.append("")
    lines.append("**Examples:**")
    for pain_point in opportunity.pain_points[:_MAX_EVIDENCE_LINKS]:
        lines.append(f"- [{pain_point.summary}]({pain_point.raw_item.url})")
    return "\n".join(lines)


def format_digest_section(digest_date: str, entries: list[tuple[Opportunity, OpportunityBrief]]) -> str:
    """Formats `entries` as given — the caller is responsible for ranking and capping them."""
    header = f"## {digest_date}"
    if not entries:
        return f"{header}\n\nNo new Solvable Opportunities this week.\n"
    body = "\n\n".join(format_opportunity_entry(opportunity, brief) for opportunity, brief in entries)
    return f"{header}\n\n{body}\n"


def prepend_digest(path: str, section: str) -> None:
    """Newest section goes right under the title, so the newest week is always
    what you see first — the file is append-*safe* (nothing is ever deleted),
    not literally append-only."""
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
        if existing.startswith(_TITLE):
            existing = existing[len(_TITLE) :]
    with open(path, "w", encoding="utf-8") as f:
        f.write(_TITLE)
        f.write(section)
        f.write("\n")
        f.write(existing)
