# Build v1 pain-point discovery pipeline (Roblox/game dev niche)

`ready-for-agent`

## Problem Statement

Pain points that Roblox/game dev community members express on Reddit and the Roblox DevForum are scattered across dozens of posts and comments a day. Spotting a *recurring* pain point — one that shows up across many different people, not just a single vent — currently requires manually reading and remembering everything, which doesn't scale and misses patterns. There's no way to turn "this problem keeps coming up" into a structured, evaluable opportunity without doing that reading and synthesis by hand.

## Solution

An automated pipeline ingests new posts/comments from a fixed set of Roblox/game dev Reddit communities and the Roblox DevForum on a daily cadence, uses an LLM to classify individual posts/comments as Pain Points, automatically clusters related Pain Points into Opportunities, filters out anything not judged Solvable by a solo developer, and ranks the rest by frequency. The top Opportunities are written up as Opportunity Briefs (problem summary, evidence, frequency, solution sketch, solvability rationale, competitor check, effort estimate) and delivered as a weekly Digest file, capped at the top 5. Each Opportunity also gets a GitHub Issue so the operator can mark it Rejected (suppressing it from future Digests) using GitHub's own UI. The system only surfaces opportunities for a human build/no-build decision — it does not build software itself.

## User Stories

1. As the operator, I want the pipeline to pull new posts and comments from r/robloxgamedev, r/roblox_dev, and r/RobloxDevelopers daily, so that new community activity is captured without me manually checking Reddit.
2. As the operator, I want the pipeline to pull new topics and posts from the Roblox DevForum daily, so that forum-only discussions are captured too.
3. As the operator, I want ingestion to use the official Reddit API (an OAuth "script" app) rather than scraping, so that the pipeline stays within Reddit's terms of service.
4. As the operator, I want ingestion to use the DevForum's public Discourse API rather than scraping, so that the pipeline stays within DevForum's terms of service.
5. As the operator, I want the ingestion job to run on a daily schedule via GitHub Actions, so that it doesn't depend on my personal machine being on.
6. As the operator, I want raw ingested posts/comments stored in SQLite, so that downstream processing has durable, queryable state.
7. As the operator, I want the SQLite database committed back to the repo after each run, so that state persists across ephemeral GitHub Actions runners.
8. As the operator, I want each new post/comment classified by an LLM as expressing a Pain Point or not, so that I only see genuine frustration/unmet needs rather than every post.
9. As the operator, I want the classifier to reject generic venting with no underlying unmet need, so that low-signal complaints don't pollute the pipeline.
10. As the operator, I want the LLM classification step to run through a provider-agnostic interface, so that I can swap the underlying model later without rewriting the classification logic.
11. As the operator, I want newly classified Pain Points automatically clustered by semantic similarity into Opportunities, so that recurring themes surface instead of one-off complaints.
12. As the operator, I want a new Pain Point that matches an existing Opportunity's theme added to that Opportunity rather than creating a duplicate, so that frequency counts stay accurate.
13. As the operator, I want a new Pain Point with no matching existing Opportunity to start a new Opportunity, so that novel themes aren't lost.
14. As the operator, I want each Opportunity judged by the LLM as Solvable or not (buildable by a solo developer) before it's eligible to surface, so that I'm not shown problems only Roblox Corp can fix.
15. As the operator, I want unsolvable Opportunities excluded entirely rather than just ranked lower, so that the Digest stays focused on things I could actually build.
16. As the operator, I want Solvable Opportunities ranked primarily by frequency (distinct Pain Points / distinct users), so that the most-recurring problems surface first.
17. As the operator, I want each surfaced Opportunity to include a plain-language problem summary, so that I can quickly understand the underlying issue.
18. As the operator, I want each Opportunity Brief to include representative quotes and links back to source posts, so that I can sanity-check the LLM's judgment against the original context.
19. As the operator, I want each Opportunity Brief to show the frequency signal (distinct Pain Points/users, time window), so that I can gauge real demand.
20. As the operator, I want each Opportunity Brief to include a rough, non-binding solution sketch, so that I have a starting point for what a tool addressing this might look like.
21. As the operator, I want each Opportunity Brief to include the LLM's solvability rationale, so that I understand why it was judged buildable by a solo dev.
22. As the operator, I want each Opportunity Brief to include a competitor check produced via live web search, so that I know whether existing tools already solve this problem before I invest time.
23. As the operator, I want each Opportunity Brief to include a t-shirt-size effort estimate (S/M/L/XL) with a one-line rationale, calibrated to me being a software engineer, so that I can triage quickly without false-precision hour estimates.
24. As the operator, I want a weekly Digest markdown file appended with new/updated Opportunities since the last Digest, so that I have a predictable, skimmable reading cadence separate from the daily ingestion cadence.
25. As the operator, I want the Digest capped at the top 5 Opportunities by frequency, so that a noisy week doesn't bury the best signal.
26. As the operator, I want the Digest committed to the public repo, so that I can read it anywhere via GitHub.
27. As the operator, I want each new Opportunity to automatically get a GitHub Issue, so that I can review/reject it using GitHub's own UI, including on mobile.
28. As the operator, I want closing (or labeling) an Opportunity's GitHub Issue to mark it Rejected, so that I have a lightweight way to dismiss ideas I don't want to pursue.
29. As the operator, I want Rejected Opportunities suppressed from future Digests, so that dismissed ideas don't keep reappearing.
30. As the operator, I want the system to NOT learn or bias future ranking based on rejections in v1, so that ranking logic stays simple and predictable until there's enough history to justify something smarter.
31. As the operator, I want the system limited to Roblox/game dev coaching as the v1 Target Niche, so that I can judge output quality with my own expertise before generalizing to other niches.
32. As the operator, I want the system to only ever produce Opportunity Briefs, never working code or prototypes, so that the build/no-build judgment call stays with me.
33. As the operator, I want the LLM and web-search calls to go through a provider-agnostic interface defaulting to Claude, so that I can swap models or search providers later via configuration, not a rewrite.
34. As the operator, I want the entire pipeline runnable via a scheduled GitHub Actions workflow in Python with SQLite state, so that it runs independent of any single machine being powered on.
35. As the operator, I want the orchestrator tested end-to-end against fixture data with fake Source/LLM-Search/Tracker adapters, so that pipeline logic (classification wiring, clustering, filtering, ranking, digest capping, rejection suppression) is verified quickly and deterministically in CI.
36. As the operator, I want separate contract/integration tests that make real calls to the Reddit API and the LLM provider, so that I catch breakage in the actual external integrations (auth, schema drift, rate limits) that fake-based tests can't detect.
37. As the operator, I want the real-API integration tests gated separately from the fast fake-based suite, so that day-to-day CI stays fast while live-integration regressions are still caught.

## Implementation Decisions

- **Language/runtime**: Python. State store: SQLite, committed back to the repo by the workflow after each run (GitHub Actions runners are ephemeral — persistence has to be explicit; see ADR-0002).
- **Hosting**: a scheduled GitHub Actions workflow. Daily trigger runs ingestion + classification + clustering + filtering/ranking; the Digest build step runs weekly (either a separate weekly-cron workflow, or a day-of-week conditional inside the daily workflow — implementer's call, no strong preference recorded).
- **Ports (the seam the tests are built around)**:
  - **Source port** — a common interface for "fetch new posts/comments since last run." `RedditSource` and `DevForumSource` are the real adapters (official Reddit API via a registered OAuth "script" app; DevForum's public Discourse API). No Discord adapter in v1.
  - **LLM/Search port** (ADR-0003) — `generate(...)` for classification/clustering/brief-writing, `search(...)` for the competitor check. Default adapter: Claude via the Anthropic API, using Claude's built-in web search tool for `search(...)`. Adapters are swappable via configuration.
  - **Tracker port** — `create_issue(...)`, `get_issue_status(...)`. Real adapter talks to GitHub Issues (via the `gh` CLI or GitHub API) in the same repo the pipeline lives in.
- **Domain records in SQLite**: raw ingested posts/comments; Pain Point records (source reference + classification result); Opportunity records (member Pain Point ids, frequency count, Solvable flag + rationale, ranking score); Opportunity Brief content; a mapping from Opportunity to its GitHub Issue number and last-known Rejected status.
- **Clustering**: on each ingestion run, newly classified Pain Points are compared against existing non-Rejected Opportunities; a semantic match appends to that Opportunity, otherwise a new Opportunity is created.
- **Filter/ranking**: only Opportunities the LLM judges Solvable are eligible for the Digest. Among those, primary sort is frequency (distinct Pain Points / distinct users). Non-Solvable Opportunities are excluded, not just deprioritized.
- **Digest build**: once weekly, select new/meaningfully-updated Solvable, non-Rejected Opportunities, cap at the top 5 by frequency, append a new dated section to the running Digest markdown file, and commit it.
- **Rejected status**: read from the corresponding GitHub Issue's state (closed/labeled) at Digest-build time; Rejected Opportunities are excluded from that run's Digest but retained in SQLite for history. No ranking-bias learning from rejections in v1.
- **Secrets**: Reddit API client id/secret, Anthropic API key, and a GitHub token (for Issue creation) are stored as GitHub Actions repository secrets — never committed.
- **Repo visibility**: public (explicit choice — see ADR-0002).

## Testing Decisions

- Good tests here assert on **external behavior** — what ends up in SQLite, what the Digest file contains, which Issues get created/read — not on internal function call sequences.
- **Primary suite: orchestrator tests.** Drive the top-level orchestrator end-to-end against fixture Reddit/DevForum payloads, with fake Source, LLM/Search, and Tracker adapters (deterministic, scripted responses) and a real temp-file SQLite database underneath. Assert on: which posts became Pain Points, how Pain Points clustered into Opportunities, that non-Solvable Opportunities were excluded, that ranking/frequency ordering is correct, that the Digest is capped at 5 and formatted correctly, and that a Rejected Opportunity (per the fake Tracker) is suppressed. This suite runs on every push/PR and stays fast since nothing hits a real network.
- **Secondary suite: adapter contract/integration tests.** Separate tests exercise the real `RedditSource`, `DevForumSource`, and the default Claude LLM/Search adapter against the actual live APIs (small, read-only calls) to catch auth breakage, schema drift, or rate-limit changes that fixture-based fakes can't detect. These require live credentials (the same GitHub Actions secrets) and are gated separately from the fast suite — e.g. a distinct CI job or an `integration` test marker — so routine CI stays quick.
- No prior test suite exists in this repo yet; this is the first spec, so there's no in-repo prior art to follow. pytest is the assumed framework given the Python runtime decision.

## Out of Scope

- Autonomously building/prototyping a solution for any Opportunity (ADR-0001) — the system stops at the Opportunity Brief.
- Discord as an ingestion source.
- Any niche other than Roblox/game dev coaching for v1 (construction, electrical, daycare, software-tooling niches are explicitly deferred).
- Learning or ranking bias derived from Rejected history.
- Email or any delivery mechanism other than the committed Digest markdown file.
- A dashboard or review UI beyond GitHub Issues + the Digest file.

## Further Notes

- This spec covers the v1 pipeline end-to-end as designed in the `/grill-with-docs` session recorded in `CONTEXT.md` and ADRs 0001–0003.
- Generalizing to other niches, adding a feedback-learning loop, or adding Discord as a source are natural v2 candidates once v1 has run long enough to prove the pipeline finds real signal.
- Exact DevForum categories to watch and the precise semantic-clustering technique (embeddings vs. an LLM matching prompt) are left to the implementer's judgment — no architectural decision was made that constrains either choice.
- **Deviation from configured convention**: `docs/agents/issue-tracker.md` specifies specs are published as GitHub Issues with the `ready-for-agent` label. This spec was instead committed to `docs/specs/` and opened as a PR because `gh` CLI wasn't available at the time. Once `gh` is set up, consider re-filing this as an actual Issue with the `ready-for-agent` label so `/triage` and future agent runs pick it up the way the convention expects.
