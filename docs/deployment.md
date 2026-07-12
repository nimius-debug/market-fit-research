# Running the pipeline unattended

Two scheduled GitHub Actions workflows drive the pipeline (ticket 6), both weekly on Mondays:

- **`.github/workflows/weekly-ingestion.yml`** — runs `pain-point-pipeline ingest` at 08:17 UTC. Pulls new Reddit posts, classifies Pain Points, clusters Opportunities, judges Solvability, generates briefs, and opens GitHub Issues for newly-Solvable Opportunities.
- **`.github/workflows/weekly-digest.yml`** — runs `pain-point-pipeline digest` at 15:23 UTC, after that day's ingestion. Builds the capped, ranked Digest from already-ingested Opportunities and refreshes Rejected status from Issue state.

Ingestion runs weekly rather than daily because of the RapidAPI free-tier quota — see below.

Both commit their own state (`data/pipeline.sqlite3`, and `DIGEST.md` for the digest workflow) back to the repo at the end of the run.

## Required repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | ingestion | **Required.** Anthropic API key for Claude (classification, clustering, briefs, competitor search) |
| `RAPIDAPI_KEY` | ingestion | **Recommended — see below.** RapidAPI Hub key for the reddit34 API. Takes priority over the `REDDIT_*` secrets when both are set. |
| `RAPIDAPI_HOST` | ingestion | Optional, defaults to `reddit34.p.rapidapi.com` — only needed if you switch to a different RapidAPI Reddit listing |
| `REDDIT_CLIENT_ID` | ingestion | **Optional — see below.** From a registered Reddit OAuth "script" app |
| `REDDIT_CLIENT_SECRET` | ingestion | Optional, same app |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for every run — nothing to add. The workflows request `contents: write` and `issues: write`/`issues: read` permissions so that token can push commits and manage Issues.

### Two ways to read Reddit: official API (blocked) vs. RapidAPI (paid stopgap)

Reddit's Responsible Builder Policy (2026) requires manual approval before new
official API credentials work — self-service registration alone is no longer
enough, and the unauthenticated `.json` endpoints are blocked for datacenter IPs
like GitHub Actions runners. A Data Access Request for this project has been
submitted to Reddit and is still pending.

Until it lands, set `RAPIDAPI_KEY` to unblock ingestion via
[reddit34 on RapidAPI Hub](https://rapidapi.com) — a paid third-party proxy
that fronts Reddit's own JSON. `cli._build_sources` picks `RedditRapidAPISource`
automatically whenever `RAPIDAPI_KEY` is set. Because it's a reseller rather
than Reddit itself, treat it as a stopgap: its pricing, rate limits, and ToS
aren't under Reddit's control and can change without notice.

**Free-tier quota drives the cadence.** The free RapidAPI plan for reddit34 caps
out at **50 requests/month**. Each ingestion run costs 1 request per
(subreddit, endpoint) pair — `RedditRapidAPISource`'s two default subreddits
(`AI_Agents`, `automation`) x two endpoints (posts + comments) = **4 requests
per run**. That's why ingestion is scheduled weekly, not daily: 4 x 4 weeks =
16/month, leaving headroom for manual `workflow_dispatch` reruns. Running
ingestion daily at this subreddit count would cost ~120/month — over 2x the
free quota — so don't switch the cron back to daily without either shrinking
`DEFAULT_SUBREDDITS` further, dropping an endpoint, or upgrading the RapidAPI
plan.

When (if) official approval lands: register the approved app's credentials as
the two `REDDIT_*` secrets above and remove `RAPIDAPI_KEY`. `_build_sources`
prefers `RAPIDAPI_KEY` when both are set, so the official adapter only takes
over once the RapidAPI key is unset — and RedditSource isn't quota-limited the
same way, so ingestion cadence could move back to daily at that point.

**Never commit a RapidAPI or Reddit key to this repo — it's public.** Set these
only as GitHub Actions repository secrets or in a local, gitignored `.env`.

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
