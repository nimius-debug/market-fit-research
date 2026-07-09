# Tickets: v1 pain-point discovery pipeline

Breaks down `docs/specs/0001-v1-pipeline.md` into tracer-bullet tickets. Ticket 1 is a walking skeleton — the whole pipeline wired end-to-end with fake adapters standing in for every external system. Tickets 2-5 each swap one fake for its real adapter and can run in parallel once ticket 1 lands. Ticket 6 wires the real adapters into scheduled automation.

Work the **frontier**: any ticket whose blockers are all done. Ticket 1 first, then 2/3/4/5 in any order (or parallel), then 6.

> **Note on tracker deviation**: `docs/agents/issue-tracker.md` specifies tickets are published as GitHub Issues (one per ticket, `ready-for-agent` label) via `gh`. `gh` CLI wasn't installable at the time this breakdown was produced, so these are recorded here as local files instead, on the same `spec/v1-pipeline` branch as the spec they implement. Once `gh` is available, re-file each of these as a real Issue with the `ready-for-agent` label, referencing `docs/specs/0001-v1-pipeline.md` as the parent spec.

## 1. Walking skeleton: end-to-end pipeline with fake adapters

**What to build:** SQLite schema and domain models (Pain Point, Opportunity, Opportunity Brief), the Source/LLM-Search/Tracker port interfaces from ADR-0003, and the orchestrator wiring ingestion → classification → clustering → Solvable filter → frequency ranking → brief generation → Digest build (capped at 5, appended, dated sections) → Issue creation — all driven by fake adapters returning fixture/scripted data. This is the seam the whole test strategy is built around (see spec's Testing Decisions).

**Blocked by:** None — can start immediately.

- [ ] SQLite schema exists for raw posts/comments, Pain Points, Opportunities, Opportunity Briefs, and the Opportunity→Issue/Rejected mapping
- [ ] Source, LLM/Search, and Tracker port interfaces are defined per ADR-0003, with fake implementations for tests
- [ ] Orchestrator runs the full pipeline end-to-end against fixture Reddit/DevForum payloads using the fakes
- [ ] A Pain Point that matches an existing Opportunity's theme is appended to it; a novel one starts a new Opportunity
- [ ] Opportunities the fake LLM judges non-Solvable are excluded from output entirely
- [ ] Solvable Opportunities are ranked primarily by frequency (distinct Pain Points/users)
- [ ] Digest file is generated with the correct dated section, capped at the top 5 Opportunities, and includes all Opportunity Brief fields (summary, evidence, frequency, solution sketch, solvability rationale, competitor check, effort estimate)
- [ ] A Rejected Opportunity (per the fake Tracker) is suppressed from the Digest
- [ ] Orchestrator test suite passes, asserting on stored SQLite state, Digest content, and fake Issue-creation calls — no real network calls

## 2. Real Reddit ingestion

**What to build:** `RedditSource` adapter using the official Reddit API (OAuth "script" app), pulling new posts/comments from r/robloxgamedev, r/roblox_dev, and r/RobloxDevelopers into the real pipeline in place of the fake Source.

**Blocked by:** 1

- [ ] `RedditSource` authenticates via a registered OAuth "script" app (no scraping)
- [ ] Fetches new posts/comments since the last run from all three subreddits
- [ ] Live-API contract test pulls at least one real post/comment and asserts it's shaped correctly for the pipeline
- [ ] Orchestrator runs against real Reddit data end-to-end (other ports can remain fake)

## 3. Real DevForum ingestion

**What to build:** `DevForumSource` adapter using the Roblox DevForum's public Discourse API, pulling new topics/posts into the real pipeline in place of the fake Source.

**Blocked by:** 1

- [ ] `DevForumSource` reads from DevForum's public Discourse API (no scraping, no auth required for public categories)
- [ ] Fetches new topics/posts since the last run
- [ ] Live-API contract test pulls at least one real topic/post
- [ ] Orchestrator runs against real DevForum data end-to-end (other ports can remain fake)

## 4. Real Claude LLM/Search adapter

**What to build:** the real `generate()`/`search()` adapter (Anthropic API + Claude's built-in web search tool), replacing the fake for Pain Point classification, Opportunity clustering, Solvable judgment, brief writing (problem summary, solution sketch, solvability rationale, t-shirt-size effort estimate), and the competitor check.

**Blocked by:** 1

- [ ] `generate()` classifies a post/comment as a Pain Point (or not) via a real Claude call
- [ ] `generate()` judges Opportunity clustering and Solvable status via real Claude calls
- [ ] `generate()` writes the full Opportunity Brief (summary, solution sketch, solvability rationale, effort estimate) via a real Claude call
- [ ] `search()` performs a live web search for the competitor check via Claude's web search tool
- [ ] Live-API contract tests cover each of the above against fixture text
- [ ] Adapter is swappable via configuration per ADR-0003 (not hardcoded at call sites)

## 5. Real GitHub Issues tracker

**What to build:** the real `Tracker` adapter (`create_issue`, `get_issue_status`) via the `gh` CLI or GitHub API — each new Opportunity gets a real Issue in this repo, and Rejected status is read from real Issue state (closed/labeled) at Digest-build time.

**Blocked by:** 1

- [ ] `create_issue` opens a real GitHub Issue for a new Opportunity
- [ ] `get_issue_status` correctly reports Rejected when the corresponding Issue is closed or labeled
- [ ] Live test creates and closes a real issue in the repo and confirms the pipeline picks up the Rejected status
- [ ] Rejected Opportunities are excluded from the next Digest but retained in SQLite

## 6. Scheduled GitHub Actions automation

**What to build:** the daily ingestion workflow and weekly Digest-build workflow running unattended on GitHub Actions, wired to all real adapters, with secrets configured (Reddit client id/secret, Anthropic API key, GitHub token) and the SQLite DB + Digest file committed back to the repo after each run.

**Blocked by:** 2, 3, 4, 5

- [ ] Daily workflow runs ingestion + classification + clustering + filter/ranking against real Reddit + DevForum + Claude
- [ ] Weekly workflow (or day-of-week conditional in the daily workflow) builds and appends the capped Digest, using real Issue/Rejected state
- [ ] Secrets are read from GitHub Actions repository secrets, never committed
- [ ] SQLite DB and Digest file are committed back to the repo after each run
- [ ] A full scheduled run produces a real Digest entry end-to-end with no manual intervention
