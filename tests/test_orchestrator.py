from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest
from fakes import FakeLLMSearch, FakeSocialQueue, FakeSource, FakeTracker, FakeVideoRenderer
from pain_point_pipeline import orchestrator, repository
from pain_point_pipeline.models import PainPoint, RawItem
from pain_point_pipeline.orchestrator import run_digest_build, run_ingestion_batch, run_recluster, run_social_draft
from pain_point_pipeline.ports import ClusterMatch, PainPointClassification


def _clustered_items(make_item, base: str, authors: list[str], **kwargs):
    """First item defines the Opportunity (title = its summary); the rest cluster into it."""
    items = [make_item(f"PAINPOINT {base}", author=authors[0], **kwargs)]
    for author in authors[1:]:
        items.append(make_item(f"PAINPOINT agree CLUSTER_WITH:PAINPOINT {base}", author=author, **kwargs))
    return items


def test_full_batch_creates_pain_point_opportunity_brief_issue_and_digest_entry(conn, now, make_item, digest_path):
    # Three distinct authors on the same problem: crosses MIN_DISTINCT_AUTHORS,
    # so it gets the brief, the issue, and a digest entry.
    source = FakeSource("reddit", _clustered_items(make_item, "scripting is painful", ["alice", "bob", "carol"]))
    llm = FakeLLMSearch()
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], llm, tracker, conn, now)

    assert ingest_result.new_raw_items == 3
    assert ingest_result.new_pain_points == 3
    assert ingest_result.new_opportunities == 1
    assert len(ingest_result.issues_created) == 1
    (issue_number,) = ingest_result.issues_created.values()
    assert tracker.titles[issue_number] == "PAINPOINT scripting is painful (3 reports, 3 people)"

    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert len(digest_result.included_opportunity_ids) == 1
    digest_text = Path(digest_path).read_text(encoding="utf-8")
    assert "Recurring problem" in digest_text
    assert "No direct competitors found" in digest_text
    assert "S — Small, well-scoped tool" in digest_text


def test_below_the_author_gate_no_brief_or_issue_until_the_third_voice_joins(conn, now, make_item):
    tracker = FakeTracker()
    first, second = _clustered_items(make_item, "scripting is painful", ["alice", "bob"])
    third = make_item(
        "PAINPOINT agree CLUSTER_WITH:PAINPOINT scripting is painful",
        author="carol",
        created_at=now + timedelta(days=3),
    )

    first_run = run_ingestion_batch([FakeSource("reddit", [first, second])], FakeLLMSearch(), tracker, conn, now)

    # Two voices: judged solvable, but no brief and no issue yet.
    assert first_run.issues_created == {}
    (opportunity_id,) = first_run.touched_opportunity_ids
    assert repository.load_opportunity(conn, opportunity_id).solvable is True
    assert repository.load_brief(conn, opportunity_id) is None

    second_run = run_ingestion_batch(
        [FakeSource("reddit", [third])], FakeLLMSearch(), tracker, conn, now + timedelta(days=7)
    )

    # Third distinct voice crosses the gate: brief and issue arrive together.
    assert opportunity_id in second_run.issues_created
    assert repository.load_brief(conn, opportunity_id) is not None


def test_issue_title_counts_refresh_as_the_opportunity_grows(conn, now, make_item):
    tracker = FakeTracker()
    items = _clustered_items(make_item, "scripting is painful", ["alice", "bob", "carol"])
    result = run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    (issue_number,) = result.issues_created.values()
    assert tracker.titles[issue_number] == "PAINPOINT scripting is painful (3 reports, 3 people)"

    fourth = make_item(
        "PAINPOINT agree CLUSTER_WITH:PAINPOINT scripting is painful",
        author="dave",
        created_at=now + timedelta(days=3),
    )
    run_ingestion_batch([FakeSource("reddit", [fourth])], FakeLLMSearch(), tracker, conn, now + timedelta(days=7))

    assert tracker.titles[issue_number] == "PAINPOINT scripting is painful (4 reports, 4 people)"


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
    # Six gate-crossing opportunities (3 authors each); claim 3 gets a fourth voice.
    items = []
    for n in range(1, 7):
        items.extend(
            _clustered_items(make_item, f"unique claim {n}", [f"alice{n}", f"bob{n}", f"carol{n}"])
        )
    booster = make_item("PAINPOINT agree CLUSTER_WITH:PAINPOINT unique claim 3", author="dave")
    source = FakeSource("reddit", [*items, booster])
    tracker = FakeTracker()

    run_ingestion_batch([source], FakeLLMSearch(), tracker, conn, now)
    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert len(digest_result.included_opportunity_ids) == 5
    boosted = repository.load_opportunity(conn, digest_result.included_opportunity_ids[0])
    assert boosted.title == "PAINPOINT unique claim 3"
    assert boosted.frequency == 4


def test_digest_excludes_opportunities_below_the_author_gate(conn, now, make_item, digest_path):
    # Solvable but only two voices: no brief, so no digest entry either.
    items = _clustered_items(make_item, "quiet problem", ["alice", "bob"])
    tracker = FakeTracker()

    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert digest_result.included_opportunity_ids == []


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


def test_opportunities_stranded_unjudged_by_a_killed_run_are_refreshed_next_run(conn, now, make_item):
    # Run 1 gets through classification/clustering but dies before phase 3
    # reaches this opportunity: pain point + opportunity committed, solvable
    # still NULL. Simulate by doing phase 2's writes directly.
    repository.create_opportunity(conn, "opp-stranded", title="stranded", now=now)
    for n, author in enumerate(["alice", "bob", "carol"], start=1):
        item = make_item(f"PAINPOINT stranded before solvability {n}", author=author)
        repository.insert_raw_item_if_new(conn, item)
        repository.insert_pain_point(
            conn, PainPoint(id=f"pp-{n}", raw_item=item, summary="stranded", created_at=now)
        )
        repository.add_pain_point_to_opportunity(conn, "opp-stranded", f"pp-{n}", now)
        repository.mark_raw_item_processed(conn, item.id, now)
    conn.commit()

    tracker = FakeTracker()
    later = now + timedelta(days=7)
    result = run_ingestion_batch([FakeSource("reddit", [])], FakeLLMSearch(), tracker, conn, later)

    # Nothing new was fetched or classified, but the stranded opportunity got judged and surfaced.
    assert result.new_pain_points == 0
    assert "opp-stranded" in result.issues_created
    opportunity = repository.load_opportunity(conn, "opp-stranded")
    assert opportunity.solvable is True


def test_already_judged_opportunities_are_not_rejudged(conn, now, make_item):
    item = make_item("PAINPOINT scripting is painful")
    tracker = FakeTracker()
    run_ingestion_batch([FakeSource("reddit", [item])], FakeLLMSearch(), tracker, conn, now)

    class _NoJudgingLLM(FakeLLMSearch):
        def judge_solvable(self, pain_points):
            raise AssertionError("judge_solvable should not run for an unchanged opportunity")

    later = now + timedelta(days=7)
    result = run_ingestion_batch([FakeSource("reddit", [])], _NoJudgingLLM(), tracker, conn, later)

    assert result.issues_created == {}


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
    items = _clustered_items(make_item, "scripting is painful", ["alice", "bob", "carol"])
    source = FakeSource("reddit", items)
    tracker = FakeTracker()

    ingest_result = run_ingestion_batch([source], FakeLLMSearch(), tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    issue_number = tracker.created[opportunity_id]
    tracker.set_status(issue_number, "rejected")

    digest_result = run_digest_build(conn, tracker, digest_path, now)

    assert digest_result.included_opportunity_ids == []
    assert "No new Solvable Opportunities" in Path(digest_path).read_text(encoding="utf-8")


def test_recluster_rebuilds_opportunities_and_closes_stale_issues(conn, now, make_item):
    # Two separate opportunities under the old criterion, one with an issue.
    items = [
        *_clustered_items(make_item, "problem A", ["alice", "bob", "carol"]),
        make_item("PAINPOINT problem B standalone", author="dana"),
    ]
    tracker = FakeTracker()
    ingest = run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    assert len(ingest.issues_created) == 1

    class _MergeEverythingLLM(FakeLLMSearch):
        """Simulates a looser criterion: every pain point matches the first candidate."""

        def match_or_create_opportunity(self, summary, candidates):
            if candidates:
                return ClusterMatch(opportunity_id=candidates[0].id)
            return super().match_or_create_opportunity(summary, candidates)

    result = run_recluster(_MergeEverythingLLM(), tracker, conn, now + timedelta(days=1))

    assert result.issues_closed == 1
    assert tracker.closed  # the stale issue was closed on GitHub's side too
    assert result.pain_points_reclustered == 4
    assert result.opportunities_created == 1  # everything merged under the looser criterion

    # Pain points survived untouched; the derived layer was rebuilt from them.
    pain_points = conn.execute("SELECT COUNT(*) AS n FROM pain_points").fetchone()["n"]
    assert pain_points == 4
    assert conn.execute("SELECT COUNT(*) AS n FROM opportunity_issues").fetchone()["n"] == 0
    # Everything awaits solvability judgment on the next ingest run.
    assert len(repository.opportunities_needing_refresh(conn)) == 1


def test_match_candidates_are_capped_by_recency_but_heavy_hitters_never_age_out(conn, now, make_item):
    # Five opportunities created oldest-to-newest; "old-heavy" is oldest but has
    # the most pain points.
    for i, opportunity_id in enumerate(["old-heavy", "a", "b", "c", "d"]):
        created = now + timedelta(minutes=i)
        repository.create_opportunity(conn, opportunity_id, title=opportunity_id, now=created)
    for n in range(3):
        item = make_item(f"heavy pain {n}", author=f"user{n}")
        repository.insert_raw_item_if_new(conn, item)
        repository.insert_pain_point(
            conn, PainPoint(id=f"heavy-pp-{n}", raw_item=item, summary="heavy", created_at=now)
        )
        # Link without bumping updated_at, so "old-heavy" stays the least recent.
        conn.execute(
            "INSERT INTO opportunity_pain_points (opportunity_id, pain_point_id) VALUES (?, ?)",
            ("old-heavy", f"heavy-pp-{n}"),
        )
    conn.commit()

    candidates = repository.list_match_candidates(conn, recent_limit=3, top_limit=1)
    candidate_ids = {c.id for c in candidates}

    assert candidate_ids == {"d", "c", "b", "old-heavy"}  # 3 most recent ∪ top-1 by frequency


def test_unchanged_opportunity_is_not_redigested_but_an_updated_one_is(conn, now, make_item, digest_path):
    source = FakeSource("reddit", _clustered_items(make_item, "scripting is painful", ["alice", "bob", "carol"]))
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
        "PAINPOINT agree CLUSTER_WITH:PAINPOINT scripting is painful",
        created_at=a_week_later,
        author="dave",
    )
    run_ingestion_batch([FakeSource("reddit", [booster])], llm, tracker, conn, a_week_later)

    third_digest = run_digest_build(conn, tracker, digest_path, a_week_later + timedelta(days=1))
    assert third_digest.included_opportunity_ids == [opportunity_id]


def test_social_draft_picks_a_qualifying_opportunity_and_marks_it_used(
    conn, now, make_item, social_draft_path
):
    # 6 distinct authors: crosses both the 3-author brief gate and the >5-report social bar.
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "APIs change without warning", authors)
    tracker = FakeTracker()
    ingest_result = run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    assert repository.list_viral_candidates(conn) == [opportunity_id]

    result = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, now)

    assert result.opportunity_id == opportunity_id
    draft_text = Path(social_draft_path).read_text(encoding="utf-8")
    assert "Hook (fixture): PAINPOINT APIs change without warning" in draft_text
    # Marked used: no longer a candidate for a future run.
    assert repository.list_viral_candidates(conn) == []


def test_social_draft_requires_more_than_five_reports(conn, now, make_item, social_draft_path):
    # Exactly 5 distinct authors/reports -- crosses the 3-author brief gate but not the >5 bar.
    authors = ["alice", "bob", "carol", "dave", "erin"]
    items = _clustered_items(make_item, "quiet problem", authors)
    tracker = FakeTracker()
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)

    assert repository.list_viral_candidates(conn) == []
    result = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, now)

    assert result.opportunity_id is None
    assert not Path(social_draft_path).exists()


def test_social_draft_writes_nothing_when_the_llm_picks_none(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "VIRAL_NONE marked problem", authors)
    tracker = FakeTracker()
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    assert len(repository.list_viral_candidates(conn)) == 1

    result = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, now)

    assert result.opportunity_id is None
    assert not Path(social_draft_path).exists()
    # Not marked used -- an LLM "not this one" isn't the same as "already posted".
    assert len(repository.list_viral_candidates(conn)) == 1


def test_social_draft_never_reuses_an_opportunity_across_runs(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "repeat-proof problem", authors)
    tracker = FakeTracker()
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)

    first = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, now)
    assert first.opportunity_id is not None

    later = now + timedelta(days=2)
    second = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, later)

    assert second.opportunity_id is None  # the only candidate was already used


def test_social_draft_saves_a_queue_row_and_pushes_it_to_the_queue(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "queued problem", authors)
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), FakeTracker(), conn, now)
    queue = FakeSocialQueue()

    result = run_social_draft(FakeLLMSearch(), queue, None, conn, social_draft_path, now)

    assert result.opportunity_id is not None
    (pushed,) = queue.pushed
    assert pushed.opportunity_id == result.opportunity_id
    # The queue carries the same publish-ready strings the markdown file shows.
    draft_text = Path(social_draft_path).read_text(encoding="utf-8")
    assert pushed.linkedin_post in draft_text
    assert pushed.x_thread in draft_text
    assert pushed.link.startswith("https://")
    stored = repository.load_social_queue_entry(conn, result.opportunity_id)
    assert stored is not None
    assert stored.linkedin_post == pushed.linkedin_post
    assert stored.queued_at == now


def test_social_draft_with_no_queue_still_saves_the_row_unqueued(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "offline problem", authors)
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), FakeTracker(), conn, now)

    result = run_social_draft(FakeLLMSearch(), None, None, conn, social_draft_path, now)

    stored = repository.load_social_queue_entry(conn, result.opportunity_id)
    assert stored is not None
    assert stored.queued_at is None


def test_social_draft_commits_the_row_before_a_webhook_failure_surfaces(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "flaky webhook problem", authors)
    ingest_result = run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), FakeTracker(), conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    queue = FakeSocialQueue(fail_with=RuntimeError("webhook down"))

    with pytest.raises(RuntimeError, match="webhook down"):
        run_social_draft(FakeLLMSearch(), queue, None, conn, social_draft_path, now)

    # Draft file + queue row survived the failure; queued_at stays NULL so
    # social-approve can re-send it.
    assert Path(social_draft_path).exists()
    stored = repository.load_social_queue_entry(conn, opportunity_id)
    assert stored is not None
    assert stored.queued_at is None


def test_social_draft_renders_the_video_and_queues_its_url(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "animated problem", authors)
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), FakeTracker(), conn, now)
    queue = FakeSocialQueue()
    renderer = FakeVideoRenderer()

    result = run_social_draft(FakeLLMSearch(), queue, renderer, conn, social_draft_path, now)

    ((script, slug),) = renderer.rendered
    assert slug == result.opportunity_id
    # Counts on screen come from the Opportunity itself, not the LLM.
    assert script.reports == 6
    assert script.people == 6
    (pushed,) = queue.pushed
    assert pushed.video_url == f"https://example.com/videos/{now.date().isoformat()}-{slug}.mp4"
    stored = repository.load_social_queue_entry(conn, result.opportunity_id)
    assert stored.video_url == pushed.video_url
    assert pushed.video_url in Path(social_draft_path).read_text(encoding="utf-8")


def test_social_draft_queues_without_a_video_when_the_render_fails(conn, now, make_item, social_draft_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "unrenderable problem", authors)
    run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), FakeTracker(), conn, now)
    queue = FakeSocialQueue()
    renderer = FakeVideoRenderer(fail_with=RuntimeError("ffmpeg exploded"))

    result = run_social_draft(FakeLLMSearch(), queue, renderer, conn, social_draft_path, now)

    # Render failure is best-effort: the draft still queues, text-only.
    assert result.opportunity_id is not None
    (pushed,) = queue.pushed
    assert pushed.video_url == ""
    stored = repository.load_social_queue_entry(conn, result.opportunity_id)
    assert stored.video_url == ""
    assert "### Video" not in Path(social_draft_path).read_text(encoding="utf-8")


def test_list_viral_candidates_excludes_rejected_opportunities(conn, now, make_item, digest_path):
    authors = ["alice", "bob", "carol", "dave", "erin", "frank"]
    items = _clustered_items(make_item, "rejected problem", authors)
    tracker = FakeTracker()
    ingest_result = run_ingestion_batch([FakeSource("reddit", items)], FakeLLMSearch(), tracker, conn, now)
    (opportunity_id,) = ingest_result.touched_opportunity_ids
    issue_number = tracker.created[opportunity_id]
    tracker.set_status(issue_number, "rejected")
    # list_viral_candidates reads Rejected status from the DB, not the live
    # tracker directly -- run_digest_build is what syncs the two (same as the
    # existing Reject-suppression tests do).
    run_digest_build(conn, tracker, digest_path, now)

    assert repository.list_viral_candidates(conn) == []
