"""Real VideoRendererPort adapter: HyperFrames render + GitHub Release upload.

Renders the video/ composition (see that directory's index.html) with the
draft's scene variables, uploads the MP4 as an asset on the rolling
`social-videos` release, and returns the asset's public download URL — the
only thing that crosses the seam, so swapping GitHub Releases for S3 later
touches nothing but this adapter.

Requires Node 22+, FFmpeg, and an authenticated `gh` (the workflow provides
GH_TOKEN); cli._build_video_renderer only constructs this when
SOCIAL_VIDEO_ENABLED is set, so local runs without the toolchain never hit it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from pain_point_pipeline.models import SceneScript
from pain_point_pipeline.video import scene_variables_json

logger = logging.getLogger(__name__)

RELEASE_TAG = "social-videos"
_RENDER_TIMEOUT_SECONDS = 600
_UPLOAD_TIMEOUT_SECONDS = 120


def _executable(name: str) -> str:
    """Windows resolves `npx`/`gh` to .cmd shims that subprocess won't find
    by bare name; shutil.which handles both platforms."""
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"{name} not found on PATH")
    return path


class HyperFramesVideoAdapter:
    def __init__(self, video_dir: str = "video", repo: str | None = None) -> None:
        self._video_dir = Path(video_dir)
        resolved = repo or os.environ.get("GITHUB_REPOSITORY", "")
        if not resolved:
            raise ValueError("GITHUB_REPOSITORY is not set and no repo was given")
        self._repo = resolved

    def render(self, script: SceneScript, slug: str) -> str:
        asset = f"{script.date}-{slug}.mp4"
        out_dir = self._video_dir / "out"
        out_dir.mkdir(exist_ok=True)
        variables_path = out_dir / f"{slug}.variables.json"
        variables_path.write_text(scene_variables_json(script), encoding="utf-8")
        output_path = out_dir / asset

        self._run(
            [
                _executable("npx"),
                "hyperframes",
                "render",
                "--quality",
                "high",
                "--variables-file",
                str(variables_path.resolve()),
                "--output",
                str(output_path.resolve()),
            ],
            timeout=_RENDER_TIMEOUT_SECONDS,
        )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"hyperframes render produced no output at {output_path}")

        self._run(
            [
                _executable("gh"),
                "release",
                "upload",
                RELEASE_TAG,
                str(output_path.resolve()),
                "--clobber",
            ],
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
        return f"https://github.com/{self._repo}/releases/download/{RELEASE_TAG}/{asset}"

    def _run(self, cmd: list[str], timeout: int) -> None:
        result = subprocess.run(
            cmd,
            cwd=self._video_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error("%s failed:\n%s\n%s", " ".join(cmd[:3]), result.stdout[-2000:], result.stderr[-2000:])
            raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd[0]} exited with {result.returncode}")
