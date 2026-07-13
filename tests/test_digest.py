"""Unit tests for digest.py's formatting and file-writing behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pain_point_pipeline.digest import (
    _MAX_EVIDENCE_LINKS,
    format_digest_section,
    format_opportunity_entry,
    prepend_digest,
)
from pain_point_pipeline.models import Opportunity, OpportunityBrief, PainPoint, RawItem


def _make_pain_point(n: int) -> PainPoint:
    raw = RawItem(
        id=f"raw-{n}",
        source="reddit",
        external_id=f"ext-{n}",
        author=f"user{n}",
        url=f"https://reddit.com/example/{n}",
        text=f"pain point {n}",
        created_at=datetime(2026, 7, 13, 12, 0, 0),
    )
    return PainPoint(id=f"pp-{n}", raw_item=raw, summary=f"Summary {n}", created_at=raw.created_at)


def _make_opportunity(pain_point_count: int) -> Opportunity:
    return Opportunity(
        id="opp-1",
        title="Example problem",
        pain_points=[_make_pain_point(n) for n in range(pain_point_count)],
        solvable=True,
        created_at=datetime(2026, 7, 13, 12, 0, 0),
        updated_at=datetime(2026, 7, 13, 12, 0, 0),
    )


def _make_brief() -> OpportunityBrief:
    return OpportunityBrief(
        opportunity_id="opp-1",
        problem_summary="Short problem.",
        solution_sketch="Short fix.",
        effort_size="S",
        effort_rationale="Small.",
        competitor_check="Not really.",
        generated_at=datetime(2026, 7, 13, 12, 0, 0),
    )


def test_entry_caps_evidence_links_even_with_more_pain_points() -> None:
    opportunity = _make_opportunity(pain_point_count=5)
    entry = format_opportunity_entry(opportunity, _make_brief())

    assert entry.count("- [Summary") == _MAX_EVIDENCE_LINKS


def test_entry_uses_plain_field_labels() -> None:
    entry = format_opportunity_entry(_make_opportunity(1), _make_brief())

    assert "**Problem:**" in entry
    assert "**Fix idea:**" in entry
    assert "**Effort:**" in entry
    assert "**Already out there?**" in entry
    # Old, wordier labels should be gone.
    assert "Solution sketch" not in entry
    assert "Solvability" not in entry
    assert "Competitor check" not in entry


def test_prepend_digest_puts_newest_section_first(tmp_path: Path) -> None:
    path = str(tmp_path / "DIGEST.md")

    first = format_digest_section("2026-07-06", [])
    prepend_digest(path, first)
    second = format_digest_section("2026-07-13", [(_make_opportunity(1), _make_brief())])
    prepend_digest(path, second)

    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("# Digest\n\n")
    assert text.index("## 2026-07-13") < text.index("## 2026-07-06")


def test_prepend_digest_creates_the_file_with_a_title(tmp_path: Path) -> None:
    path = str(tmp_path / "DIGEST.md")

    prepend_digest(path, format_digest_section("2026-07-13", []))

    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("# Digest\n\n## 2026-07-13")
