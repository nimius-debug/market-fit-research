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
| `ANTHROPIC_API_KEY` | ingestion | **Required.** Anthropic API key for Claude (classification, clustering, briefs, competitor search) |
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

## Run time, timeouts, and resumability

The first live 6-subreddit run exceeded the job's 20-minute timeout: ~1,200
fetched items were classified one LLM call at a time, sequentially, and because
the run committed its SQLite state once at the very end, the kill discarded
*everything* — including the `since` watermark, so the next run would have
refetched and re-timed-out identically, forever. Three changes fix this class
of problem:

- **Fetch is capped at 50 items per (subreddit, endpoint)** (down from 100) —
  fetch volume directly drives LLM call count, which is what actually costs
  time and money. Worst case is now ~600 items on a cold start, a fraction of
  that on later runs thanks to the watermark.
- **Classification runs in a thread pool** (8 concurrent calls, batches of 25),
  cutting wall-clock time roughly 5-8x. Clustering stays sequential by design:
  each new Pain Point can create the Opportunity the next one should match.
- **Runs are resumable.** Ingestion commits after every fetch, classification
  batch, and Opportunity refresh, and classification is driven by a
  `processed_at` marker on each raw item rather than by "what did this run
  fetch". A killed run keeps everything it finished; the next run picks up
  only the unprocessed remainder — items are never re-classified once marked.
  The workflow puts the timeout on the *ingest step* (25 min) rather than only
  the job, so the `always()` commit step still pushes partial state to the
  repo when ingestion runs long.

## Cost control: model selection

The pipeline defaults to **`claude-haiku-4-5`**, Anthropic's cheapest tier (~5×
cheaper than Opus), which handles the short classification/clustering judgments
fine at this volume. To trade money for judgment quality, set a `CLAUDE_MODEL`
repository **variable** (or add it to the workflow `env`) — e.g.
`claude-sonnet-5` (mid) or `claude-opus-4-8` (max). The adapter picks the right
web-search tool variant for whichever model is set.

Two other things that keep costs down:

- **The first run is the most expensive.** With an empty database, every
  fetched item gets classified. After that, the `since` watermark means later
  runs only process genuinely new posts — typically a small fraction of the
  backfill.
- The competitor check runs a real web search per Solvable Opportunity refresh —
  that's the priciest single call. If costs still bite, capping how often an
  existing Opportunity's brief is regenerated is the next lever (not yet
  implemented; ask for it if needed).

## Testing before the first scheduled run

Both workflows also trigger on `workflow_dispatch`, so you can run either one manually from the Actions tab once secrets are set, rather than waiting for the next cron firing.
