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


def test_sources_are_devforum_only_without_reddit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["devforum"]


def test_empty_reddit_credentials_also_mean_devforum_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions passes empty strings for unset secrets, not missing env vars.
    monkeypatch.setenv("REDDIT_CLIENT_ID", "")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["devforum"]


def test_reddit_joins_the_sources_when_credentials_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["devforum", "reddit"]


def test_missing_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
