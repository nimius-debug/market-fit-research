"""Builds the explainer video's scene script — the render input for the
HyperFrames composition in video/ (see docs/deployment.md, "Social drafts").

The template is fixed; only this data changes per post. The counts and the
disclosure are injected here, deterministically, never written by the LLM —
the same rule social.py applies to evidence links.

HyperFrames receives data as flat, typed composition variables (its
`--variables-file` mechanism; there is no array type), so the step captions
travel as step1..step3 — empty string means "no third step" and the template
skips the row.
"""

from __future__ import annotations

import json

from pain_point_pipeline.models import Opportunity, SceneScript
from pain_point_pipeline.phrasing import report_tail_for, verb_for
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import DISCLOSURE

MAX_STEPS = 3

# The broken-loop scene always shows exactly three boxes (the template's
# animation — X mark, shake — is built around that shape). A weak LLM
# response falls back to these generic labels per box, never a blank one.
LOOP_BOXES = 3
DEFAULT_LOOP = ("Try", "Get stuck", "Start over")
DEFAULT_LOOP_CAPTION = "The same loop. The same dead end. Every week."


def _loop_labels(labels: tuple[str, ...]) -> tuple[str, ...]:
    padded = list(labels[:LOOP_BOXES]) + [""] * (LOOP_BOXES - min(len(labels), LOOP_BOXES))
    return tuple(label.strip() or default for label, default in zip(padded, DEFAULT_LOOP))


def build_scene_script(date: str, opportunity: Opportunity, copy: SocialDraftCopy) -> SceneScript:
    verb = verb_for(opportunity.id)
    return SceneScript(
        hook=copy.video_hook,
        problem=copy.video_problem,
        reports=opportunity.frequency,
        people=opportunity.distinct_authors,
        stat_label=f"people on Reddit are {verb} this",
        reports_tail=report_tail_for(opportunity.id),
        loop_caption=copy.video_loop_caption.strip() or DEFAULT_LOOP_CAPTION,
        loop=_loop_labels(copy.video_loop),
        steps=copy.video_steps[:MAX_STEPS],
        question=copy.video_question,
        disclosure=DISCLOSURE,
        date=date,
    )


def scene_variables(script: SceneScript) -> dict[str, str | int]:
    """The composition-variable values object (keyed by variable id) the
    template in video/ declares. Key set must stay in sync with
    video/index.html's data-composition-variables declarations."""
    steps = list(script.steps) + [""] * MAX_STEPS
    loop = list(script.loop) + [""] * LOOP_BOXES
    return {
        "hook": script.hook,
        "problem": script.problem,
        "reports": script.reports,
        "people": script.people,
        "stat_label": script.stat_label,
        "reports_tail": script.reports_tail,
        "loop_caption": script.loop_caption,
        "loop1": loop[0],
        "loop2": loop[1],
        "loop3": loop[2],
        "step1": steps[0],
        "step2": steps[1],
        "step3": steps[2],
        "question": script.question,
        "disclosure": script.disclosure,
        "date": script.date,
    }


def scene_variables_json(script: SceneScript) -> str:
    """Stable key order and formatting: HyperFrames renders are deterministic,
    so identical input must stay byte-identical for golden-render tests."""
    return json.dumps(scene_variables(script), indent=2, sort_keys=True) + "\n"
