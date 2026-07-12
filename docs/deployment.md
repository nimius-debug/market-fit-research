# Running the pipeline unattended

Two scheduled GitHub Actions workflows drive the pipeline (ticket 6), both weekly on Mondays:

- **`.github/workflows/weekly-ingestion.yml`** — runs `pain-point-pipeline ingest` at 08:17 UTC. Pulls new Reddit posts, classifies Pain Points, clusters Opportunities, judges Solvability, generates briefs, and opens GitHub Issues for newly-Solvable Opportunities.
- **`.github/workflows/weekly-digest.yml`** — runs `pain-point-pipeline digest` at 15:23 UTC, after that day's ingestion. Builds the capped, ranked Digest from already-ingested Opportunities and refreshes Rejected status from Issue state.

Ingestion is scheduled weekly rather than daily — that was originally forced by
a since-removed RapidAPI adapter's 50-requests/month quota. The current default
source (Arctic Shift, see below) has no meaningful quota, so the weekly cadence
is now just an unrevisited leftover, not a hard constraint; say the word if you
want it moved back to daily.

Both commit their own state (`data/pipeline.sqlite3`, and `DIGEST.md` for the digest workflow) back to the repo at the end of the run.

## Required repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Notes |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | ingestion | **Required by default.** DeepSeek platform API key — the default LLM provider (classification, clustering, briefs, effort estimate, competitor check) |
| `ANTHROPIC_API_KEY` | ingestion | **Optional — see below.** Only needed if `LLM_PROVIDER` is set to `claude` |
| `REDDIT_CLIENT_ID` | ingestion | **Optional — see below.** From a registered Reddit OAuth "script" app |
| `REDDIT_CLIENT_SECRET` | ingestion | Optional, same app |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for every run — nothing to add. The workflows request `contents: write` and `issues: write`/`issues: read` permissions so that token can push commits and manage Issues.

### Two ways to read Reddit: Arctic Shift (default) vs. the official API (blocked)

Reddit's Responsible Builder Policy (2026) requires manual approval before new
official API credentials work — self-service registration alone is no longer
enough, and the unauthenticated `.json` endpoints are blocked for datacenter IPs
like GitHub Actions runners. A Data Access Request for this project has been
submitted to Reddit and is still pending.

Until it lands, `cli._build_sources` defaults to `ArcticShiftSource`
(`adapters/arctic_shift.py`), which reads the same public listings through
[Arctic Shift](https://arctic-shift.photon-reddit.com), a free, no-auth,
community-run Pushshift successor — no secret to configure at all. It rate-limits
around ~120k requests/hour (verified live 2026-07-12), far more than this
pipeline needs, so there's no RapidAPI-style quota shaping the cadence anymore.
The trade-off: it's community-maintained with no uptime/SLA guarantee. A missed
run just gets picked up by the next one via the `since` watermark, so that's an
acceptable risk here.

When (if) official approval lands: register the approved app's credentials as
the two `REDDIT_*` secrets above. `_build_sources` prefers `RedditSource`
over `ArcticShiftSource` whenever those are set.

**Never commit a Reddit or RapidAPI-style key to this repo — it's public.** Set
credentials only as GitHub Actions repository secrets or in a local, gitignored
`.env`.

## LLM provider: DeepSeek (default) vs. Claude

`cli._build_llm` defaults to **`DeepSeekLLMSearchAdapter`** (`adapters/deepseek.py`,
model `deepseek-v4-flash` by default), which is far cheaper than even Claude's
Haiku tier at this pipeline's classification/clustering volume. It works by
pointing the `anthropic` SDK at DeepSeek's Anthropic-API-compatible endpoint
(`https://api.deepseek.com/anthropic`) — no separate SDK needed. Set a
`DEEPSEEK_MODEL` repository variable (e.g. `deepseek-v4-pro`) to trade money for
judgment quality, same idea as `CLAUDE_MODEL` below.

Set the `LLM_PROVIDER` repository variable to `claude` to use
**`ClaudeLLMSearchAdapter`** instead (needs `ANTHROPIC_API_KEY`). The pipeline
defaults that adapter to **`claude-haiku-4-5`**, Anthropic's cheapest tier; set
`CLAUDE_MODEL` (e.g. `claude-sonnet-5` or `claude-opus-4-8`) to trade money for
quality there too.

**The one real trade-off: `check_competitors` loses live web search on
DeepSeek.** Claude's competitor check uses Anthropic's server-side hosted
`web_search` tool, which DeepSeek's compatible endpoint doesn't expose (it's
Anthropic infrastructure, not part of the Messages API surface DeepSeek
mirrors). DeepSeek's `check_competitors` instead asks the model to judge from
its own training knowledge and say so explicitly in the brief — a knowingly
weaker signal, accepted here for the cost savings on the other five, much
higher-volume calls. Switch to `LLM_PROVIDER=claude` if you want live search
back for that one field.

Two other things that keep costs down regardless of provider:

- **The first run is the most expensive.** With an empty database, every
  fetched item gets classified. After that, the `since` watermark means later
  runs only process genuinely new posts — typically a small fraction of the
  backfill. At the current 6 subreddits, expect roughly 1,200 items classified
  on that first run (~200 per subreddit).
- The competitor check is the priciest single call when using Claude (a real
  web search per Solvable Opportunity refresh); DeepSeek's version is cheap
  since it's a plain completion. If costs still bite on Claude, capping how
  often an existing Opportunity's brief is regenerated is the next lever (not
  yet implemented; ask for it if needed).

## Run time, timeouts, and resumability

The first live 6-subreddit run exceeded the job timeout: ~1,200 fetched items
classified one sequential LLM call at a time, committed once at the very end —
the kill discarded everything including the `since` watermark, so the next run
would have refetched and re-timed-out identically, forever. The design that
fixes this class of problem:

- **Fetch is capped at 50 items per (subreddit, endpoint)** — fetch volume
  directly drives LLM call count, which is what actually costs time and money.
  Worst case ~600 items on a cold start, a fraction of that on later runs
  thanks to the watermark.
- **Classification runs in a thread pool** (8 concurrent calls, batches of 25).
  Clustering stays sequential by design: each new Pain Point can create the
  Opportunity the next one should match.
- **Runs are resumable — both LLM phases.** Ingestion commits after every
  fetch, classification batch, and Opportunity refresh. Classification is
  driven by a `processed_at` marker on each raw item, and the
  solvability/brief phase by a `solvability_checked_at` marker on each
  Opportunity — neither works off "what did this run fetch", so a killed run
  keeps everything it finished and the next run picks up only the remainder.
  (The first live run proved why the second marker matters: it timed out
  mid-refresh and stranded 114 of 181 opportunities that an in-memory work
  list would never have revisited.) The workflow puts the timeout on the
  *ingest step* (25 min) rather than only the job, so the `always()` commit
  step still pushes partial state to the repo when ingestion runs long.
- **The solvability/brief phase is also parallel**: the up-to-4 LLM calls per
  Opportunity run in a thread pool (batches of 8), with all SQLite writes and
  issue creation kept on the main thread.
- **Briefs and issues open only for recurring problems.** Both the expensive
  brief trio (narrative/competitor/effort) and the GitHub Issue wait until an
  Opportunity reaches **3+ distinct authors** (`MIN_DISTINCT_AUTHORS`) — one
  person posting repeatedly never qualifies. Below the gate an Opportunity is
  still clustered and judged Solvable (one cheap call); the brief and Issue
  arrive automatically on the refresh after the third voice joins. The Digest
  draws only from briefed Opportunities, so it too shows recurring concerns
  only. Issue titles carry live counts — `… (13 reports, 5 people)` —
  refreshed whenever the Opportunity changes. Without this gate the first
  live runs were heading toward ~200 open issues, nearly all singletons.

## Reclustering after a criterion change

Clustering happens incrementally at ingest time, so a change to the match
criterion only affects *future* Pain Points — the existing Opportunity layer
stays clustered under the old rules. The manual-only **"Recluster
(maintenance)"** workflow (`.github/workflows/recluster.yml`) re-derives it:
closes every tracked GitHub issue (with an explanatory comment), wipes
Opportunities/briefs/issue links — stored Pain Points and their
classifications are kept, so nothing is re-classified — and replays every
Pain Point summary through the current matcher in arrival order. Solvability
marks start out empty, so trigger **"Weekly ingestion"** afterwards to rebuild
briefs and issues under the current gates. The operation is idempotent: if it
dies partway, just run it again.

## Testing before the first scheduled run

Both workflows also trigger on `workflow_dispatch`, so you can run either one manually from the Actions tab once secrets are set, rather than waiting for the next cron firing.
