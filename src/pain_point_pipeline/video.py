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
import re

from pain_point_pipeline.models import Opportunity, SceneScript
from pain_point_pipeline.phrasing import report_tail_for, verb_for
from pain_point_pipeline.ports import SocialDraftCopy
from pain_point_pipeline.social import DISCLOSURE

MAX_STEPS = 3

# Keep the rendered asset's filename short and readable — a few words of the
# title, not the whole thing (which can be a sentence).
_MAX_SLUG_LEN = 60


def video_asset_slug(opportunity: Opportunity) -> str:
    """A short, human-readable, filesystem-safe stem for the rendered video
    asset, taken from the Opportunity title (e.g. "Scripting is painful" ->
    "scripting-is-painful"). Falls back to the opportunity id when the title
    has no usable ASCII characters, so the asset always has a name."""
    slug = re.sub(r"[^a-z0-9]+", "-", opportunity.title.lower()).strip("-")
    if len(slug) > _MAX_SLUG_LEN:
        # Trim to the last whole word inside the limit, not mid-word.
        slug = slug[:_MAX_SLUG_LEN].rsplit("-", 1)[0] or slug[:_MAX_SLUG_LEN]
    return slug or opportunity.id


# The broken-loop scene always shows exactly three boxes (the template's
# animation — X mark, shake — is built around that shape). A weak LLM
# response falls back to these generic labels per box, never a blank one.
LOOP_BOXES = 3
DEFAULT_LOOP = ("Try", "Get stuck", "Start over")
DEFAULT_LOOP_CAPTION = "The same loop. The same dead end. Every week."

# --- AI scene images -------------------------------------------------------
# The five scene images tell one continuous story with ONE recognizable
# character. The LLM is the storyboard artist: it invents a character/subject
# that fits the topic (SceneScript.anchor) and the four beats that follow
# (SceneScript.beats) — it need not be a robot. Scene 1 is that anchor
# (text-to-image); scenes 2-5 are Kontext edits OF THAT ANCHOR (see
# ports.ImageGenPort.edit), each applying one beat. Editing from the anchor is
# what locks the character — plain text-to-image with a shared seed kept the
# style but drifted the character between shots.
IMAGE_COUNT = 5
IMAGE_SEED = 20260726
# FLUX likes dimensions that are multiples of 32; 1024x1280 is a clean 4:5,
# the composition covers it into its card (object-fit: cover).
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1280

# Cinematic 3D, dark — the chosen look. STYLE_ISOMETRIC is the testing
# fallback; point ACTIVE_STYLE at it to switch the whole set in one line.
STYLE_CINEMATIC_3D = (
    "cinematic 3D render in a cute stylized mascot style — friendly, rounded, "
    "Pixar-like character design, never photorealistic and never a realistic "
    "human face; near-black background, soft studio lighting with a single "
    "deep electric-blue key light, glossy surfaces, subtle rim light, muted "
    "palette with one electric-blue accent, generous dark empty space, highly "
    "detailed, no text, no words, no logo, no watermark"
)
STYLE_ISOMETRIC = (
    "clean isometric 3D icon illustration, near-black background, single "
    "electric-blue accent, minimal, soft ambient occlusion, generous dark "
    "empty space, no text, no words, no logo, no watermark"
)
ACTIVE_STYLE = STYLE_CINEMATIC_3D

# Framing appended to the LLM's anchor phrase: the image sits in the
# composition's card (text lives in a separate panel), so the subject fills
# the frame rather than hiding in dark space.
ANCHOR_FRAMING = "centered in frame, medium shot from the front, prominent and clearly visible"

# The four story beats (scenes 2-5) are normally written by the LLM per topic
# (SceneScript.beats). These generic, character-agnostic beats fill in for any
# the LLM left blank, so a weak response still yields a coherent arc.
BEAT_COUNT = IMAGE_COUNT - 1
DEFAULT_SCENE_BEATS = (
    "busy and a little worried as the work piles up around it",
    "overwhelmed and stuck, the same task looping around it",
    "a glowing blue shield or gate steps in front of it and blocks the trouble",
    "relaxed and happy, giving a thumbs up, calm and resolved",
)

# Prepended to every beat so Kontext preserves the anchor's exact character —
# deliberately character-agnostic (the subject may be anything the LLM chose).
_KEEP_CHARACTER = (
    "Keep the EXACT same main character — same design, same colors, same "
    "proportions — and the same dark cinematic 3D style with blue glow. Keep "
    "it centered and clearly visible. Now show it "
)


def anchor_prompt(anchor: str) -> str:
    """The text-to-image prompt for scene 1, or "" when there is no anchor (the
    renderer then skips images and uses its plain branded-gradient card)."""
    anchor = anchor.strip()
    return f"{anchor}, {ANCHOR_FRAMING}, {ACTIVE_STYLE}" if anchor else ""


def scene_edit_instructions(beats: tuple[str, ...]) -> list[str]:
    """The four Kontext edit instructions for scenes 2-5, built from the LLM's
    story beats, each wrapped so the character stays consistent. Blank or
    missing beats fall back to the generic arc, so there are always four."""
    chosen = [
        (beats[i].strip() if i < len(beats) and beats[i].strip() else DEFAULT_SCENE_BEATS[i])
        for i in range(BEAT_COUNT)
    ]
    return [_KEEP_CHARACTER + beat.rstrip(".") + "." for beat in chosen]


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
        anchor=copy.video_anchor,
        beats=copy.video_beats,
    )


def scene_variables(
    script: SceneScript, image_paths: list[str] | None = None
) -> dict[str, str | int]:
    """The composition-variable values object (keyed by variable id) the
    template in video/ declares. Key set must stay in sync with
    video/index.html's data-composition-variables declarations.

    `image_paths` are the five rendered scene images (paths the composition
    can load, relative to video/). Omitted or short → those slots are empty
    strings, and the composition shows its plain typographic background — the
    same graceful fallback used when image generation is off or fails."""
    steps = list(script.steps) + [""] * MAX_STEPS
    loop = list(script.loop) + [""] * LOOP_BOXES
    images = list(image_paths or []) + [""] * IMAGE_COUNT
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
        "image1": images[0],
        "image2": images[1],
        "image3": images[2],
        "image4": images[3],
        "image5": images[4],
    }


def scene_variables_json(script: SceneScript, image_paths: list[str] | None = None) -> str:
    """Stable key order and formatting: HyperFrames renders are deterministic,
    so identical input must stay byte-identical for golden-render tests."""
    return json.dumps(scene_variables(script, image_paths), indent=2, sort_keys=True) + "\n"
