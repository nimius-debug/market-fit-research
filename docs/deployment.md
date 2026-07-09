# Running the pipeline unattended

Two scheduled GitHub Actions workflows drive the pipeline (ticket 6):

- **`.github/workflows/daily-ingestion.yml`** — runs `pain-point-pipeline ingest` daily. Pulls new Reddit/DevForum posts, classifies Pain Points, clusters Opportunities, judges Solvability, generates briefs, and opens GitHub Issues for newly-Solvable Opportunities.
- **`.github/workflows/weekly-digest.yml`** — runs `pain-point-pipeline digest` weekly. Builds the capped, ranked Digest from already-ingested Opportunities and refreshes Rejected status from Issue state.

Both commit their own state (`data/pipeline.sqlite3`, and `DIGEST.md` for the digest workflow) back to the repo at the end of the run.

## Required repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | daily ingestion | Anthropic API key for Claude (classification, clustering, briefs, competitor search) |
| `REDDIT_CLIENT_ID` | daily ingestion | From a registered Reddit OAuth "script" app |
| `REDDIT_CLIENT_SECRET` | daily ingestion | Same app |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for every run — nothing to add. The workflows request `contents: write` and `issues: write`/`issues: read` permissions so that token can push commits and manage Issues.

## Testing before the first scheduled run

Both workflows also trigger on `workflow_dispatch`, so you can run either one manually from the Actions tab once secrets are set, rather than waiting for the next cron firing.
