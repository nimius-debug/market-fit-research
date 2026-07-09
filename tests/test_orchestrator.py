from __future__ import annotations

from pathlib import Path

from fakes import FakeLLMSearch, FakeSource, FakeTracker
from pain_point_pipeline import repository
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch


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
