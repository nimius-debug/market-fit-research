"""Unit tests for social.py's formatting and file-writing behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pain_point_pipeline.models import Opportunity, PainPoint, RawItem
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import format_social_draft, prepend_social_draft


def _make_pain_point(url: str = "https://reddit.com/example/1") -> PainPoint:
    raw = RawItem(
        id="raw-1",
        source="reddit",
        external_id="ext-1",
        author="alice",
        url=url,
        text="pain point",
        created_at=datetime(2026, 7, 14, 12, 0, 0),
    )
    return PainPoint(id="pp-1", raw_item=raw, summary="Summary", created_at=raw.created_at)


def _make_opportunity(with_pain_point: bool = True) -> Opportunity:
    return Opportunity(
        id="opp-1",
        title="APIs change without warning",
        pain_points=[_make_pain_point()] if with_pain_point else [],
        solvable=True,
        created_at=datetime(2026, 7, 14, 12, 0, 0),
        updated_at=datetime(2026, 7, 14, 12, 0, 0),
    )


def _make_copy() -> SocialDraftCopy:
    return SocialDraftCopy(
        x_hook="Nobody warns you when this breaks.",
        x_body=("Body one.", "Body two."),
        x_closer="Here's where I found it.",
        linkedin_post="LinkedIn body text.",
    )


def test_link_only_appears_on_the_final_tweet() -> None:
    section = format_social_draft("2026-07-14", _make_opportunity(), _make_copy())

    x_section = section.split("### X (thread)")[1].split("### LinkedIn (post)")[0]
    # Link appears exactly once within the X thread (on the closer, not the hook/body).
    assert x_section.count("https://reddit.com/example/1") == 1
    closer_line = [line for line in x_section.splitlines() if "Here's where I found it" in line][0]
    assert "https://reddit.com/example/1" in closer_line


def test_linkedin_post_has_no_link_but_comment_does() -> None:
    section = format_social_draft("2026-07-14", _make_opportunity(), _make_copy())

    post_section = section.split("### LinkedIn (post)")[1].split("### LinkedIn (first comment")[0]
    comment_section = section.split("### LinkedIn (first comment")[1]

    assert "https://reddit.com/example/1" not in post_section
    assert "Source: https://reddit.com/example/1" in comment_section


def test_x_thread_is_numbered_in_order() -> None:
    section = format_social_draft("2026-07-14", _make_opportunity(), _make_copy())

    assert "1. Nobody warns you when this breaks." in section
    assert "2. Body one." in section
    assert "3. Body two." in section
    assert "4. Here's where I found it. https://reddit.com/example/1" in section


def test_handles_an_opportunity_with_no_pain_points_gracefully() -> None:
    section = format_social_draft("2026-07-14", _make_opportunity(with_pain_point=False), _make_copy())

    assert "(no source link available)" in section


def test_prepend_social_draft_puts_newest_first(tmp_path: Path) -> None:
    path = str(tmp_path / "SOCIAL_DRAFTS.md")

    first = format_social_draft("2026-07-07", _make_opportunity(), _make_copy())
    prepend_social_draft(path, first)
    second = format_social_draft("2026-07-14", _make_opportunity(), _make_copy())
    prepend_social_draft(path, second)

    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("# Social Drafts\n\n")
    assert text.index("## 2026-07-14") < text.index("## 2026-07-07")
