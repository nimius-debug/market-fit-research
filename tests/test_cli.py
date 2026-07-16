from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from fakes import FakeSocialQueue
from pain_point_pipeline import cli, db, repository
from pain_point_pipeline.models import SocialQueueEntry


def test_ingest_dispatches_to_run_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["ingest"])

    assert exit_code == 0
    assert calls == ["ingest"]


def test_digest_dispatches_to_run_weekly_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))

    exit_code = cli.main(["digest"])

    assert exit_code == 0
    assert calls == ["digest"]


def test_social_draft_dispatches_to_run_social_draft_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(cli, "run_ingestion", lambda: calls.append("ingest"))
    monkeypatch.setattr(cli, "run_weekly_digest", lambda: calls.append("digest"))
    monkeypatch.setattr(cli, "run_social_draft_command", lambda: calls.append("social-draft"))

    exit_code = cli.main(["social-draft"])

    assert exit_code == 0
    assert calls == ["social-draft"]


def _seed_queue_row(db_path: str, queued_at: datetime | None = None) -> SocialQueueEntry:
    conn = db.connect(db_path)
    conn.execute(
        "INSERT INTO opportunities (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("opp-1", "APIs change without warning", "2026-07-14T12:00:00", "2026-07-14T12:00:00"),
    )
    entry = SocialQueueEntry(
        opportunity_id="opp-1",
        date="2026-07-14",
        linkedin_post="LinkedIn body.",
        x_thread="1. Tweet.",
        link="https://reddit.com/example/1",
        queued_at=queued_at,
    )
    repository.save_social_queue_entry(conn, entry)
    conn.commit()
    conn.close()
    return entry


def test_social_approve_pushes_the_stored_row_and_marks_it_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "pipeline.sqlite3")
    _seed_queue_row(db_path)
    queue = FakeSocialQueue()
    monkeypatch.setattr(cli, "_build_social_queue", lambda: queue)

    exit_code = cli.run_social_approve_command("opp-1", db_path=db_path)

    assert exit_code == 0
    assert [entry.opportunity_id for entry in queue.pushed] == ["opp-1"]
    conn = db.connect(db_path)
    stored = repository.load_social_queue_entry(conn, "opp-1")
    conn.close()
    assert stored is not None and stored.queued_at is not None


def test_social_approve_is_a_noop_when_already_queued_unless_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "pipeline.sqlite3")
    _seed_queue_row(db_path, queued_at=datetime(2026, 7, 14, 13, 0, 0))
    queue = FakeSocialQueue()
    monkeypatch.setattr(cli, "_build_social_queue", lambda: queue)

    assert cli.run_social_approve_command("opp-1", db_path=db_path) == 0
    assert queue.pushed == []

    assert cli.run_social_approve_command("opp-1", force=True, db_path=db_path) == 0
    assert [entry.opportunity_id for entry in queue.pushed] == ["opp-1"]


def test_social_approve_errors_on_an_unknown_opportunity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "pipeline.sqlite3")
    monkeypatch.setattr(cli, "_build_social_queue", lambda: FakeSocialQueue())

    assert cli.run_social_approve_command("missing", db_path=db_path) == 1


def test_social_approve_errors_without_a_webhook_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAKE_WEBHOOK_URL", raising=False)

    assert cli.run_social_approve_command("opp-1", db_path=str(tmp_path / "pipeline.sqlite3")) == 1


def _clear_reddit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)


def test_arctic_shift_used_without_official_reddit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["reddit"]
    assert isinstance(sources[0], cli.ArcticShiftSource)


def test_arctic_shift_used_when_credentials_are_empty_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions passes empty strings for unset secrets, not missing env vars.
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "")

    sources = cli._build_sources()

    assert isinstance(sources[0], cli.ArcticShiftSource)


def test_official_reddit_used_when_official_credentials_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "test-id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "test-secret")

    sources = cli._build_sources()

    assert [source.name for source in sources] == ["reddit"]
    assert isinstance(sources[0], cli.RedditSource)


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_PROVIDER", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_deepseek_used_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    llm = cli._build_llm()

    assert isinstance(llm, cli.DeepSeekLLMSearchAdapter)


def test_claude_used_when_llm_provider_is_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    llm = cli._build_llm()

    assert isinstance(llm, cli.ClaudeLLMSearchAdapter)


def test_missing_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_unknown_command_exits_nonzero() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
