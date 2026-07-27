"""Real ImageGenPort adapter: fal.ai for the explainer video's scene images.

Two models, one per method:
- generate() -> FLUX 2 Pro text-to-image, for scene 1 (the anchor).
- edit() -> FLUX Kontext, for scenes 2-5: each takes the anchor plus an
  instruction and returns the SAME character in a new beat, so the whole story
  stays one recognizable robot (see video.py's ANCHOR/SCENE_EDITS).

The reference image is passed to Kontext as a base64 data URI rather than
fal's file-upload CDN (that endpoint was timing out from here; data URIs are
documented and reliable).

fal_client is imported lazily so the rest of the pipeline (and the test suite)
never needs the package installed; only the render host with FAL_KEY set and
scene images turned on ever constructs this adapter. FAL_KEY is read by
fal_client from the environment, the same way DEEPSEEK_API_KEY is.
"""

from __future__ import annotations

import base64
import logging
import os

import requests

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "fal-ai/flux-2-pro"
KONTEXT_MODEL = "fal-ai/flux-pro/kontext"
# Kontext takes a preset ratio, not width/height; 3:4 is the closest to the
# anchor's 4:5 — the composition's cover-fit absorbs the small difference.
EDIT_ASPECT_RATIO = "3:4"
_DOWNLOAD_TIMEOUT_SECONDS = 60


class FalImageGenAdapter:
    def __init__(self, model: str = DEFAULT_MODEL, kontext_model: str = KONTEXT_MODEL) -> None:
        if not os.environ.get("FAL_KEY"):
            raise RuntimeError("FAL_KEY must be set (a fal.ai API key)")
        self._model = model
        self._kontext_model = kontext_model

    def generate(self, prompt: str, seed: int, width: int, height: int, out_path: str) -> None:
        result = self._subscribe(
            self._model,
            {
                "prompt": prompt,
                "seed": seed,
                "image_size": {"width": width, "height": height},
                "output_format": "jpeg",
                # A public post: keep fal's safety checker on so obviously
                # unsafe generations fail loudly rather than reaching the Sheet.
                "enable_safety_checker": True,
            },
        )
        self._download(self._first_image_url(result, prompt), out_path)

    def edit(self, instruction: str, reference_path: str, out_path: str) -> None:
        with open(reference_path, "rb") as handle:
            data_uri = "data:image/jpeg;base64," + base64.b64encode(handle.read()).decode()
        result = self._subscribe(
            self._kontext_model,
            {
                "prompt": instruction,
                "image_url": data_uri,
                "aspect_ratio": EDIT_ASPECT_RATIO,
                "output_format": "jpeg",
            },
        )
        self._download(self._first_image_url(result, instruction), out_path)

    def _subscribe(self, model: str, arguments: dict) -> object:
        import fal_client  # lazy: keeps fal-client out of the base install

        return fal_client.subscribe(model, arguments=arguments, with_logs=False)

    @staticmethod
    def _first_image_url(result: object, prompt: str) -> str:
        images = result.get("images") if isinstance(result, dict) else None
        if not images or not images[0].get("url"):
            raise RuntimeError(f"fal returned no image for prompt {prompt[:60]!r}")
        return images[0]["url"]

    def _download(self, url: str, out_path: str) -> None:
        response = requests.get(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
        with open(out_path, "wb") as handle:
            handle.write(response.content)
