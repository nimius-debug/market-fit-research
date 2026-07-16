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
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import DISCLOSURE

MAX_STEPS = 3


def build_scene_script(date: str, opportunity: Opportunity, copy: SocialDraftCopy) -> SceneScript:
    return SceneScript(
        hook=copy.video_hook,
        problem=copy.video_problem,
        reports=opportunity.frequency,
        people=opportunity.distinct_authors,
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
    return {
        "hook": script.hook,
        "problem": script.problem,
        "reports": script.reports,
        "people": script.people,
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
