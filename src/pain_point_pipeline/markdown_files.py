"""Shared prepend-newest-first writer for the pipeline's human-facing markdown
files (DIGEST.md, SOCIAL_DRAFTS.md): new content goes right under the title,
so the top of the file is always what's current. Nothing is ever deleted —
append-*safe*, not literally append-only.
"""

from __future__ import annotations

import os


def prepend_section(path: str, title: str, section: str) -> None:
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
        if existing.startswith(title):
            existing = existing[len(title) :]
    with open(path, "w", encoding="utf-8") as f:
        f.write(title)
        f.write(section)
        f.write("\n")
        f.write(existing)
