"""Formats and appends the weekly Digest file (CONTEXT.md: Digest)."""

from __future__ import annotations

import os

from pain_point_pipeline.models import Opportunity, OpportunityBrief

MAX_OPPORTUNITIES_PER_DIGEST = 5


def format_opportunity_entry(opportunity: Opportunity, brief: OpportunityBrief) -> str:
    lines = [
        f"### {opportunity.title}",
        "",
        f"**Frequency:** {opportunity.frequency} Pain Points from {opportunity.distinct_authors} distinct users",
        "",
        f"**Problem:** {brief.problem_summary}",
        "",
        f"**Solution sketch:** {brief.solution_sketch}",
        "",
        f"**Solvability:** {opportunity.solvable_rationale}",
        "",
        f"**Competitor check:** {brief.competitor_check}",
        "",
        f"**Effort estimate:** {brief.effort_size} — {brief.effort_rationale}",
        "",
        "**Evidence:**",
    ]
    for pain_point in opportunity.pain_points[:3]:
        lines.append(f"- [{pain_point.summary}]({pain_point.raw_item.url})")
    return "\n".join(lines)


def format_digest_section(digest_date: str, entries: list[tuple[Opportunity, OpportunityBrief]]) -> str:
    """Formats `entries` as given — the caller is responsible for ranking and capping them."""
    header = f"## {digest_date}"
    if not entries:
        return f"{header}\n\nNo new Solvable Opportunities this week.\n"
    body = "\n\n".join(format_opportunity_entry(opportunity, brief) for opportunity, brief in entries)
    return f"{header}\n\n{body}\n"


def append_digest(path: str, section: str) -> None:
    is_new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# Digest\n\n")
        f.write(section)
        f.write("\n")
