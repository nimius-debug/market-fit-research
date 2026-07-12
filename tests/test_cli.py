from __future__ import annotations

import pytest

from pain_point_pipeline import cli


def test_ingest_dispatches_to_run_weekly_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_weekly_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["ingest"])

    assert exit_code == 0
    assert calls == ["ingest"]


def test_digest_dispatches_to_run_weekly_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_weekly_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["digest"])

    assert exit_code == 0
    assert calls == ["digest"]


def _clear_reddit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("RAPIDAPI_KEY", "RAPIDAPI_HOST", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def test_no_sources_without_any_reddit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)

    sources = cli._build_sources()

    assert sources == []


def test_empty_credentials_also_mean_no_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions passes empty strings for unset secrets, not missing env vars.
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    monkeypatch.setenv("REDDIT_CLIENT_ID", "")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")

    sources = cli._build_sources()

    assert sources == []


def test_rapidapi_reddit_used_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["reddit"]
    assert isinstance(sources[0], cli.RedditRapidAPISource)


def test_official_reddit_used_when_only_official_credentials_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["reddit"]
    assert isinstance(sources[0], cli.RedditSource)


def test_rapidapi_takes_priority_when_both_credential_sets_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("RAPIDAPI_KEY", "test-key")
    monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")

    sources = cli._build_sources()

    assert isinstance(sources[0], cli.RedditRapidAPISource)


def test_missing_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
