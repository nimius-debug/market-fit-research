from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pytest

from pain_point_pipeline import db
from pain_point_pipeline.models import RawItem

NOW = datetime(2026, 7, 9, 12, 0, 0)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(str(tmp_path / "pipeline.sqlite3"))
    yield connection
    connection.close()


@pytest.fixture
def digest_path(tmp_path: Path) -> str:
    return str(tmp_path / "DIGEST.md")


@pytest.fixture
def social_draft_path(tmp_path: Path) -> str:
    return str(tmp_path / "SOCIAL_DRAFTS.md")


@pytest.fixture
def make_item() -> Callable[..., RawItem]:
    def _make_item(
        text: str,
        *,
        source: str = "reddit",
        author: str = "alice",
        created_at: datetime = NOW - timedelta(hours=1),
        external_id: str | None = None,
    ) -> RawItem:
        return RawItem(
            id=str(uuid.uuid4()),
            source=source,
            external_id=external_id or str(uuid.uuid4()),
            author=author,
            url=f"https://example.com/{source}/{external_id or 'x'}",
            text=text,
            created_at=created_at,
        )

    return _make_item
