from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fakes import FakeLLMSearch, FakeSource, FakeTracker
from pain_point_pipeline import orchestrator, repository
from pain_point_pipeline.models import RawItem
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch
from pain_point_pipeline.ports import PainPointClassification


def test_full_batch_creates_pain_point_opportunity_brief_issue_and_digest_entry(conn, now, make_item, digest_path):
    item = make_item("PAINPOINT scripting is painful")
    source = FakeSource("reddit", [item])
    llm = FakeLLMSearch()
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], llm, tracker, conn, now)

    assert ingest_result.new_raw_items == 1
    assert ingest_result.new_pain_points == 1
    assert ingest_result.new_opportunities == 1
    assert len(ingest_result.issues_created) == 1

    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert len(digest_result.included_opportunity_ids) == 1
    digest_text = Path(digest_path).read_text(encoding="utf-8")
    assert "Recurring problem" in digest_text
    assert "No direct competitors found" in digest_text
    assert "S — Small, well-scoped tool" in digest_text


def test_non_pain_point_items_are_ignored(conn, now, make_item, digest_path):
    item = make_item("just a normal post, nothing wrong here")
    source = FakeSource("reddit", [item])

    result = run_ingestion_batch([source], FakeLLMSearch(), FakeTracker(), conn, now)

    assert result.new_raw_items == 1
    assert result.new_pain_points == 0
    assert result.new_opportunities == 0


def test_matching_pain_points_cluster_into_same_opportunity(conn, now, make_item, digest_path):
    first = make_item("PAINPOINT scripting is painful", author="alice")
    second = make_item(
        "PAINPOINT another dev agrees CLUSTER_WITH:PAINPOINT scripting is painful", author="bob"
    )
    source = FakeSource("reddit", [first, second])

    result = run_ingestion_batch([source], FakeLLMSearch(), FakeTracker(), conn, now)

    assert result.new_pain_points == 2
    assert result.new_opportunities == 1
    (opportunity_id,) = result.touched_opportunity_ids
    opportunity = repository.load_opportunity(conn, opportunity_id)
    assert opportunity.frequency == 2
    assert opportunity.distinct_authors == 2


def test_unsolvable_opportunity_gets_no_brief_or_issue_and_is_excluded_from_digest(
    conn, now, make_item, digest_path
):
    item = make_item("PAINPOINT UNSOLVABLE the platform itself is broken")
    source = FakeSource("reddit", [item])
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], FakeLLMSearch(), tracker, conn, now)

    assert ingest_result.new_opportunities == 1
    assert ingest_result.issues_created == {}
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    opportunity = repository.load_opportunity(conn, opportunity_id)
    assert opportunity.solvable is False

    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert digest_result.included_opportunity_ids == []
    assert "No new Solvable Opportunities" in Path(digest_path).read_text(encoding="utf-8")


def test_digest_is_capped_at_five_ranked_by_frequency(conn, now, make_item, digest_path):
    items = [make_item(f"PAINPOINT unique claim {n}", external_id=f"item-{n}") for n in range(1, 7)]
    booster = make_item(
        "PAINPOINT extra vote CLUSTER_WITH:PAINPOINT unique claim 3", external_id="booster"
    )
    source = FakeSource("reddit", [*items, booster])
    tracker = FakeTracker()

    run_ingestion_batch([source], FakeLLMSearch(), tracker, conn, now)
    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert len(digest_result.included_opportunity_ids) == 5
    boosted = repository.load_opportunity(conn, digest_result.included_opportunity_ids[0])
    assert boosted.title == "PAINPOINT unique claim 3"
    assert boosted.frequency == 2


class _CountingLLM(FakeLLMSearch):
    """FakeLLMSearch that counts classify calls (thread-safely, since Phase 2 is parallel)."""

    def __init__(self) -> None:
        self.classify_calls = 0
        self._lock = threading.Lock()

    def classify_pain_point(self, item: RawItem) -> PainPointClassification:
        with self._lock:
            self.classify_calls += 1
        return super().classify_pain_point(item)


def test_backlog_left_by_a_killed_run_is_processed_by_the_next_run(conn, now, make_item):
    # Simulate a run killed after fetch: raw items committed, never classified.
    leftover = make_item("PAINPOINT stranded by a timeout")
    repository.insert_raw_item_if_new(conn, leftover)
    conn.commit()

    result = run_ingestion_batch([FakeSource("reddit", [])], FakeLLMSearch(), FakeTracker(), conn, now)

    assert result.new_raw_items == 0  # nothing newly fetched...
    assert result.new_pain_points == 1  # ...but the backlog item got classified


def test_already_processed_items_are_not_reclassified(conn, now, make_item):
    item = make_item("PAINPOINT scripting is painful")
    source = FakeSource("reddit", [item])
    llm = _CountingLLM()
    tracker = FakeTracker()

    run_ingestion_batch([source], llm, tracker, conn, now)
    assert llm.classify_calls == 1

    # Same source again: the item is refetched (FakeSource ignores dedup) but
    # both the (source, external_id) uniqueness and processed_at skip it.
    later = now + timedelta(days=7)
    second = run_ingestion_batch([FakeSource("reddit", [item])], llm, tracker, conn, later)

    assert llm.classify_calls == 1
    assert second.new_pain_points == 0


def test_crash_mid_run_keeps_all_previously_committed_batches(conn, now, make_item, monkeypatch):
    monkeypatch.setattr(orchestrator, "CLASSIFY_BATCH_SIZE", 2)

    class _ExplodingLLM(FakeLLMSearch):
        def classify_pain_point(self, item: RawItem) -> PainPointClassification:
            if "BOOM" in item.text:
                raise RuntimeError("simulated mid-run crash")
            return super().classify_pain_point(item)

    items = [
        make_item("PAINPOINT batch one, item one", external_id="a"),
        make_item("PAINPOINT batch one, item two", external_id="b"),
        make_item("BOOM batch two dies", external_id="c"),
    ]

    with pytest.raises(RuntimeError, match="simulated mid-run crash"):
        run_ingestion_batch([FakeSource("reddit", items)], _ExplodingLLM(), FakeTracker(), conn, now)

    # Discard the in-flight transaction, as a killed process would.
    conn.rollback()

    processed = conn.execute("SELECT COUNT(*) AS n FROM raw_items WHERE processed_at IS NOT NULL").fetchone()["n"]
    pain_points = conn.execute("SELECT COUNT(*) AS n FROM pain_points").fetchone()["n"]
    unprocessed = repository.list_unprocessed_raw_items(conn)

    assert processed == 2  # batch one survived its commit
    assert pain_points == 2
    assert [item.external_id for item in unprocessed] == ["c"]  # retried next run
    # The watermark also survived (committed during the fetch phase).
    assert repository.get_last_fetched_at(conn, "reddit") == now


def test_rejected_opportunity_is_suppressed_from_digest(conn, now, make_item, digest_path):
    item = make_item("PAINPOINT scripting is painful")
    source = FakeSource("reddit", [item])
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], FakeLLMSearch(), tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    issue_number = tracker.created[opportunity_id]
    tracker.set_status(issue_number, "rejected")

    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert digest_result.included_opportunity_ids == []
    assert "No new Solvable Opportunities" in Path(digest_path).read_text(encoding="utf-8")


def test_unchanged_opportunity_is_not_redigested_but_an_updated_one_is(conn, now, make_item, digest_path):
    item = make_item("PAINPOINT scripting is painful")
    source = FakeSource("reddit", [item])
    llm = FakeLLMSearch()
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], llm, tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids

    first_digest = run_digest_build(conn, tracker, digest_path, now)
    assert first_digest.included_opportunity_ids == [opportunity_id]

    a_week_later = now + timedelta(days=7)
    second_digest = run_digest_build(conn, tracker, digest_path, a_week_later)
    assert second_digest.included_opportunity_ids == []

    booster = make_item(
        "PAINPOINT another dev agrees CLUSTER_WITH:PAINPOINT scripting is painful",
        created_at=a_week_later,
        author="bob",
    )
    run_ingestion_batch([FakeSource("reddit", [booster])], llm, tracker, conn, a_week_later)

    third_digest = run_digest_build(conn, tracker, digest_path, a_week_later + timedelta(days=1))
    assert third_digest.included_opportunity_ids == [opportunity_id]
