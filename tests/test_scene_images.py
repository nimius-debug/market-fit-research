"""Scene-image generation: the video adapter's all-or-nothing fallback, and
the fal adapter's request/download shape (both driven by fakes — no network)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from pain_point_pipeline.adapters.hyperframes_video import HyperFramesVideoAdapter
from pain_point_pipeline.models import SceneScript
from pain_point_pipeline.video import (
    IMAGE_COUNT,
    IMAGE_HEIGHT,
    IMAGE_SEED,
    IMAGE_WIDTH,
    anchor_prompt,
    scene_edit_instructions,
)
from fakes import FakeImageGen


def _script(anchor: str = "a friendly shop-clerk character at a counter") -> SceneScript:
    return SceneScript(
        hook="h",
        problem="p",
        reports=3,
        people=2,
        loop_caption="c",
        loop=("a", "b", "c"),
        steps=("s1", "s2"),
        question="q",
        disclosure="d",
        date="2026-07-16",
        anchor=anchor,
        beats=("busy", "stuck", "helped", "happy"),
    )


def _adapter(image_gen: object | None) -> HyperFramesVideoAdapter:
    return HyperFramesVideoAdapter(repo="owner/repo", image_gen=image_gen)


def test_scene_images_anchor_then_edits_the_rest_from_it(tmp_path: Path) -> None:
    gen = FakeImageGen()
    adapter = _adapter(gen)

    paths = adapter._scene_images(_script(), "slug", tmp_path)

    # Five paths, all present on disk.
    assert paths == [f"out/slug-{i}.jpg" for i in range(1, IMAGE_COUNT + 1)]
    # Scene 1 is the text-to-image anchor, at the fixed seed and dims.
    assert len(gen.generated) == 1
    prompt, seed, width, height, anchor_path = gen.generated[0]
    assert prompt == anchor_prompt(_script().anchor)
    assert (seed, width, height) == (IMAGE_SEED, IMAGE_WIDTH, IMAGE_HEIGHT)
    # Scenes 2-5 are Kontext edits, each OF THE ANCHOR, in beat order.
    assert [instr for instr, _, _ in gen.edited] == scene_edit_instructions(_script().beats)
    assert {ref for _, ref, _ in gen.edited} == {anchor_path}


def test_scene_images_falls_back_to_none_if_any_generation_fails(tmp_path: Path) -> None:
    # All-or-nothing: a mid-set failure (here the 3rd call, a Kontext edit)
    # means the whole video renders plain, never a mix of images and blanks.
    adapter = _adapter(FakeImageGen(fail_on=3))

    assert adapter._scene_images(_script(), "slug", tmp_path) == []


def test_scene_images_empty_without_a_generator(tmp_path: Path) -> None:
    assert _adapter(None)._scene_images(_script(), "slug", tmp_path) == []


def test_scene_images_empty_without_an_anchor(tmp_path: Path) -> None:
    assert _adapter(FakeImageGen())._scene_images(_script(anchor=""), "slug", tmp_path) == []


def test_fal_adapter_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from pain_point_pipeline.adapters.fal_image import FalImageGenAdapter

    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FAL_KEY"):
        FalImageGenAdapter()


def test_fal_adapter_submits_and_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pain_point_pipeline.adapters import fal_image

    monkeypatch.setenv("FAL_KEY", "test-key")

    submitted: dict[str, object] = {}

    def fake_subscribe(model: str, arguments: dict, with_logs: bool = False) -> dict:
        submitted["model"] = model
        submitted["arguments"] = arguments
        return {"images": [{"url": "https://fal.example/out.jpg"}]}

    fake_module = types.SimpleNamespace(subscribe=fake_subscribe)
    monkeypatch.setitem(sys.modules, "fal_client", fake_module)

    class FakeResponse:
        content = b"downloaded-bytes"

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(fal_image.requests, "get", lambda url, timeout: FakeResponse())

    out = tmp_path / "img.jpg"
    fal_image.FalImageGenAdapter().generate("a robot", 42, 1024, 1280, str(out))

    assert out.read_bytes() == b"downloaded-bytes"
    assert submitted["model"] == "fal-ai/flux-2-pro"
    assert submitted["arguments"]["seed"] == 42
    assert submitted["arguments"]["image_size"] == {"width": 1024, "height": 1280}


def test_fal_adapter_edit_sends_kontext_with_a_data_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pain_point_pipeline.adapters import fal_image

    monkeypatch.setenv("FAL_KEY", "test-key")

    submitted: dict[str, object] = {}

    def fake_subscribe(model: str, arguments: dict, with_logs: bool = False) -> dict:
        submitted["model"] = model
        submitted["arguments"] = arguments
        return {"images": [{"url": "https://fal.example/edited.jpg"}]}

    monkeypatch.setitem(sys.modules, "fal_client", types.SimpleNamespace(subscribe=fake_subscribe))

    class FakeResponse:
        content = b"edited-bytes"

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(fal_image.requests, "get", lambda url, timeout: FakeResponse())

    ref = tmp_path / "anchor.jpg"
    ref.write_bytes(b"anchor-image")
    out = tmp_path / "edited.jpg"
    fal_image.FalImageGenAdapter().edit("make it calmer", str(ref), str(out))

    assert out.read_bytes() == b"edited-bytes"
    assert submitted["model"] == "fal-ai/flux-pro/kontext"
    assert submitted["arguments"]["prompt"] == "make it calmer"
    assert str(submitted["arguments"]["image_url"]).startswith("data:image/jpeg;base64,")
