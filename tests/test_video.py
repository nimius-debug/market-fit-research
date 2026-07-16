"""Unit tests for video.py's scene script building and variable mapping."""

from __future__ import annotations

import json
from datetime import datetime

from pain_point_pipeline.models import Opportunity, PainPoint, RawItem
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import DISCLOSURE
from pain_point_pipeline.video import build_scene_script, scene_variables, scene_variables_json


def _make_opportunity(authors: list[str]) -> Opportunity:
    created = datetime(2026, 7, 16, 12, 0, 0)
    pain_points = [
        PainPoint(
            id=f"pp-{i}",
            raw_item=RawItem(
                id=f"raw-{i}",
                source="reddit",
                external_id=f"ext-{i}",
                author=author,
                url=f"https://reddit.com/example/{i}",
                text="pain",
                created_at=created,
            ),
            summary="Summary",
            created_at=created,
        )
        for i, author in enumerate(authors)
    ]
    return Opportunity(
        id="opp-1", title="Title", pain_points=pain_points, created_at=created, updated_at=created
    )


def _make_copy(steps: tuple[str, ...] = ("Step one.", "Step two.")) -> SocialDraftCopy:
    return SocialDraftCopy(
        x_hook="X hook.",
        x_body=("Body.",),
        x_closer="Closer.",
        linkedin_post="Post.",
        video_hook="Video hook.",
        video_problem="Video problem.",
        video_steps=steps,
        video_question="Worth building?",
    )


def test_counts_and_disclosure_are_injected_not_llm_written() -> None:
    # 3 reports from 2 distinct people.
    script = build_scene_script("2026-07-16", _make_opportunity(["alice", "bob", "alice"]), _make_copy())

    assert script.reports == 3
    assert script.people == 2
    assert script.disclosure == DISCLOSURE


def test_scene_variables_pads_missing_steps_with_empty_strings() -> None:
    script = build_scene_script("2026-07-16", _make_opportunity(["alice"]), _make_copy(("Only step.",)))

    variables = scene_variables(script)

    assert variables["step1"] == "Only step."
    assert variables["step2"] == ""
    assert variables["step3"] == ""


def test_scene_variables_caps_steps_at_three() -> None:
    steps = ("One.", "Two.", "Three.", "Four.")
    script = build_scene_script("2026-07-16", _make_opportunity(["alice"]), _make_copy(steps))

    variables = scene_variables(script)

    assert variables["step3"] == "Three."
    assert "Four." not in variables.values()


def test_scene_variables_json_is_stable_and_round_trips() -> None:
    script = build_scene_script("2026-07-16", _make_opportunity(["alice", "bob"]), _make_copy())

    first = scene_variables_json(script)
    second = scene_variables_json(script)

    assert first == second  # byte-identical: golden-render tests depend on it
    assert json.loads(first) == scene_variables(script)
