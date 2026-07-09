from __future__ import annotations

import pytest

from pain_point_pipeline import cli


def test_ingest_dispatches_to_run_daily_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_daily_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["ingest"])

    assert exit_code == 0
    assert calls == ["ingest"]


def test_digest_dispatches_to_run_weekly_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_daily_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["digest"])

    assert exit_code == 0
    assert calls == ["digest"]


def test_missing_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
