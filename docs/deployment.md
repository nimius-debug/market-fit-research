# Running the pipeline unattended

Two scheduled GitHub Actions workflows drive the pipeline (ticket 6):

- **`.github/workflows/daily-ingestion.yml`** — runs `pain-point-pipeline ingest` daily. Pulls new Reddit/DevForum posts, classifies Pain Points, clusters Opportunities, judges Solvability, generates briefs, and opens GitHub Issues for newly-Solvable Opportunities.
- **`.github/workflows/weekly-digest.yml`** — runs `pain-point-pipeline digest` weekly. Builds the capped, ranked Digest from already-ingested Opportunities and refreshes Rejected status from Issue state.

Both commit their own state (`data/pipeline.sqlite3`, and `DIGEST.md` for the digest workflow) back to the repo at the end of the run.

## Required repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | daily ingestion | **Required.** Anthropic API key for Claude (classification, clustering, briefs, competitor search) |
| `REDDIT_CLIENT_ID` | daily ingestion | **Optional — see below.** From a registered Reddit OAuth "script" app |
| `REDDIT_CLIENT_SECRET` | daily ingestion | Optional, same app |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for every run — nothing to add. The workflows request `contents: write` and `issues: write`/`issues: read` permissions so that token can push commits and manage Issues.

### Reddit is currently disabled (approval pending)

Reddit's Responsible Builder Policy (2026) requires manual approval before new
API credentials work — self-service registration alone is no longer enough, and
the unauthenticated `.json` endpoints are blocked for datacenter IPs like GitHub
Actions runners. A Data Access Request for this project has been submitted to
Reddit; until it's approved, ingestion runs **DevForum-only**, which works with
no credentials at all.

When (if) approval lands: register the approved app's credentials as the two
`REDDIT_*` secrets above. The pipeline detects them automatically and re-enables
`RedditSource` on the next run — no code or workflow change needed.

## Cost control: model selection

The pipeline defaults to **`claude-haiku-4-5`**, Anthropic's cheapest tier (~5×
cheaper than Opus), which handles the short classification/clustering judgments
fine at this volume. To trade money for judgment quality, set a `CLAUDE_MODEL`
repository **variable** (or add it to the workflow `env`) — e.g.
`claude-sonnet-5` (mid) or `claude-opus-4-8` (max). The adapter picks the right
web-search tool variant for whichever model is set.

Two other things that keep costs down:

- **The first run is the most expensive.** With an empty database, every
  fetched item gets classified. After that, the `since` watermark means daily
  runs only process genuinely new posts — typically a small fraction of the
  backfill.
- The competitor check runs a real web search per Solvable Opportunity refresh —
  that's the priciest single call. If costs still bite, capping how often an
  existing Opportunity's brief is regenerated is the next lever (not yet
  implemented; ask for it if needed).

## Testing before the first scheduled run

Both workflows also trigger on `workflow_dispatch`, so you can run either one manually from the Actions tab once secrets are set, rather than waiting for the next cron firing.
