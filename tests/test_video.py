"""Unit tests for video.py's scene script building and variable mapping."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from pain_point_pipeline.models import Opportunity, PainPoint, RawItem
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import DISCLOSURE
from pain_point_pipeline.video import (
    ACTIVE_STYLE,
    BEAT_COUNT,
    DEFAULT_SCENE_BEATS,
    IMAGE_COUNT,
    anchor_prompt,
    build_scene_script,
    scene_edit_instructions,
    scene_variables,
    scene_variables_json,
)


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


def _make_copy(
    steps: tuple[str, ...] = ("Step one.", "Step two."),
    loop: tuple[str, ...] = ("Try this", "It breaks", "Start over"),
    loop_caption: str = "Loop caption.",
    anchor: str = "a friendly shop-clerk character at a glowing counter",
    beats: tuple[str, ...] = ("busy", "stuck", "helped", "happy"),
) -> SocialDraftCopy:
    return SocialDraftCopy(
        x_hook="X hook.",
        x_body=("Body.",),
        x_closer="Closer.",
        linkedin_post="Post.",
        video_hook="Video hook.",
        video_problem="Video problem.",
        video_loop_caption=loop_caption,
        video_loop=loop,
        video_steps=steps,
        video_question="Worth building?",
        video_anchor=anchor,
        video_beats=beats,
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


def test_loop_falls_back_per_box_when_llm_gives_too_few_labels() -> None:
    # The scene always needs 3 boxes; missing or blank labels get the generic
    # default for that position, and a blank caption gets the generic caption.
    script = build_scene_script(
        "2026-07-16", _make_opportunity(["alice"]), _make_copy(loop=("Ask AI", ""), loop_caption="  ")
    )

    assert script.loop == ("Ask AI", "Get stuck", "Start over")
    assert script.loop_caption == "The same loop. The same dead end. Every week."


def test_loop_caps_labels_at_three() -> None:
    script = build_scene_script(
        "2026-07-16", _make_opportunity(["alice"]), _make_copy(loop=("A", "B", "C", "D"))
    )

    variables = scene_variables(script)

    assert variables["loop3"] == "C"
    assert "D" not in variables.values()


def test_scene_variables_json_is_stable_and_round_trips() -> None:
    script = build_scene_script("2026-07-16", _make_opportunity(["alice", "bob"]), _make_copy())

    first = scene_variables_json(script)
    second = scene_variables_json(script)

    assert first == second  # byte-identical: golden-render tests depend on it
    assert json.loads(first) == scene_variables(script)


def test_anchor_and_beats_flow_from_copy_into_scene_script() -> None:
    script = build_scene_script(
        "2026-07-16",
        _make_opportunity(["alice"]),
        _make_copy(anchor="a tiny drone in a maze", beats=("a", "b", "c", "d")),
    )

    assert script.anchor == "a tiny drone in a maze"
    assert script.beats == ("a", "b", "c", "d")


def test_anchor_prompt_carries_the_anchor_and_style() -> None:
    prompt = anchor_prompt("a small delivery drone in glowing pipes")

    assert prompt.startswith("a small delivery drone in glowing pipes")
    assert ACTIVE_STYLE in prompt


def test_no_anchor_means_no_anchor_prompt() -> None:
    assert anchor_prompt("") == ""
    assert anchor_prompt("   ") == ""


def test_scene_edit_instructions_use_llm_beats_wrapped_to_keep_character() -> None:
    instructions = scene_edit_instructions(("swamped by tasks", "stuck looping", "helper clears it", "all calm"))

    assert len(instructions) == BEAT_COUNT
    assert all(instr.startswith("Keep the EXACT same main character") for instr in instructions)
    assert "swamped by tasks." in instructions[0]
    assert "all calm." in instructions[3]


def test_scene_edit_instructions_fall_back_for_missing_beats() -> None:
    # Only one beat given -> positions 2-4 use the generic defaults.
    instructions = scene_edit_instructions(("my custom beat",))

    assert len(instructions) == BEAT_COUNT
    assert "my custom beat." in instructions[0]
    assert DEFAULT_SCENE_BEATS[1] in instructions[1]
    assert DEFAULT_SCENE_BEATS[3] in instructions[3]


def test_scene_variables_default_to_empty_images_for_the_fallback_look() -> None:
    script = build_scene_script("2026-07-16", _make_opportunity(["alice"]), _make_copy())

    variables = scene_variables(script)

    assert [variables[f"image{i}"] for i in range(1, IMAGE_COUNT + 1)] == [""] * IMAGE_COUNT


def test_scene_variables_carry_image_paths_and_pad_missing_ones() -> None:
    script = build_scene_script("2026-07-16", _make_opportunity(["alice"]), _make_copy())

    variables = scene_variables(script, image_paths=["out/a-1.jpg", "out/a-2.jpg"])

    assert variables["image1"] == "out/a-1.jpg"
    assert variables["image2"] == "out/a-2.jpg"
    assert variables["image3"] == ""  # padded — composition hides it, shows the gradient


def test_composition_declares_exactly_the_variables_the_pipeline_supplies() -> None:
    # The docstring's promise: the id set in video/index.html's
    # data-composition-variables must match scene_variables' keys exactly, or
    # the render silently drops/misses a value. Guards both sides together.
    html = (Path(__file__).parent.parent / "video" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"data-composition-variables='(\[.*?\])'", html, re.DOTALL)
    assert match is not None, "could not find data-composition-variables in index.html"
    declared = {entry["id"] for entry in json.loads(match.group(1))}

    script = build_scene_script("2026-07-16", _make_opportunity(["alice"]), _make_copy())
    assert declared == set(scene_variables(script).keys())
