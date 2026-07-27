# Running the pipeline unattended

Three scheduled GitHub Actions workflows drive the pipeline (ticket 6):

- **`.github/workflows/ingestion.yml`** — runs `pain-point-pipeline ingest` daily at 08:17 UTC. Pulls new Reddit posts, classifies Pain Points, clusters Opportunities, judges Solvability, generates briefs, and opens GitHub Issues for newly-Solvable Opportunities. Daily (not weekly) so the social-draft candidate pool refills at the same cadence posts go out; the `since` watermark keeps each run cheap — only genuinely new posts get classified.
- **`.github/workflows/weekly-digest.yml`** — runs `pain-point-pipeline digest` Mondays at 15:23 UTC, after that day's ingestion. Builds the capped, ranked Digest from already-ingested Opportunities and refreshes Rejected status from Issue state.
- **`.github/workflows/social-draft.yml`** — runs `pain-point-pipeline social-draft` daily at 12:30 UTC, after that day's ingestion. Picks at most one Opportunity worth a social post, writes a draft to `SOCIAL_DRAFTS.md`, and queues it to the approval Google Sheet. See its own section below.

Both commit their own state (`data/pipeline.sqlite3`, and `DIGEST.md` for the digest workflow) back to the repo at the end of the run.

## Required repository secrets

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Used by | Notes |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | ingestion | **Required by default.** DeepSeek platform API key — the default LLM provider (classification, clustering, briefs, effort estimate, competitor check) |
| `ANTHROPIC_API_KEY` | ingestion | **Optional — see below.** Only needed if `LLM_PROVIDER` is set to `claude` |
| `REDDIT_CLIENT_ID` | ingestion | **Optional — see below.** From a registered Reddit OAuth "script" app |
| `REDDIT_CLIENT_SECRET` | ingestion | Optional, same app |
| `MAKE_WEBHOOK_URL` | social draft | **Optional.** Make.com webhook that feeds the posting-approval Google Sheet (see "Social drafts" below). Unset: drafts still land in `SOCIAL_DRAFTS.md`, they just aren't queued |

The social-draft workflow also sets `SOCIAL_VIDEO_ENABLED=true` (plain env in
the workflow file, not a secret) to turn on explainer-video rendering, and
passes the automatic `GITHUB_TOKEN` as `GH_TOKEN` so the render can upload the
MP4 to the `social-videos` release. Local runs leave both unset and skip the
video entirely.

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
marks start out empty, so trigger **"Daily ingestion"** afterwards to rebuild
briefs and issues under the current gates. The operation is idempotent: if it
dies partway, just run it again.

## Social drafts: audience-building content, human-approved in a Google Sheet

`social-draft.yml` runs daily and picks **at most one** Opportunity per run
worth turning into a social post — a genuinely different job from the Digest
(which surfaces *build* candidates) or Issues (the review queue). This is
public content under your name; the design deliberately keeps a human in the
loop and avoids redistributing anyone's actual words:

- **Candidates need more than 5 reports** (`repository.SOCIAL_MIN_REPORTS`) —
  stricter than the 3-author Issue/Brief gate, since a public, twice-published
  post is a higher bar than a private review-queue item — and must never have
  been used for a social post before (`opportunities.social_posted_at`, set
  permanently once picked, so nothing repeats).
- **An LLM picks the single best candidate**, or decides none of them are
  worth it — `pick_viral_opportunity` judges relatability, how sharp the core
  tension is, and how easy the fix is to picture. A `null` pick is a normal,
  expected outcome, not an error; most runs may write nothing.
- **No verbatim quotes, no usernames.** `write_social_draft` only ever sees
  the already-paraphrased Opportunity Brief, never raw Reddit text or author
  names. The one link back to the source thread is appended by the pipeline
  itself, from the Opportunity's own evidence URL — never generated by the
  LLM, so it can't be hallucinated or pointed at the wrong thread.
- **Format**: an X thread (hook → 1-2 body tweets → closing tweet with the
  link) plus a LinkedIn post with the link held back as a separate "first
  comment" field — both platforms' algorithms suppress reach on posts with
  outbound links, so the link lives one click away instead of in the main
  post/tweet. Voice is Hormozi-style hook-writing (lead with the sharpest
  real number, short sentences, problem-agitate-then-fix, zero
  throat-clearing) but strictly truthful: the first-person speaker is only
  ever the curator ("I run a system that tracks what people complain
  about") — the prompt forbids invented personal experience ("I built 3
  apps and...") outright. Same 5th-grade `PLAIN_LANGUAGE_STYLE` as the rest
  of the pipeline's user-facing text.
- **Nothing posts without a human flip in the Sheet.** `SOCIAL_DRAFTS.md`
  (prepended newest-first, same pattern as `DIGEST.md`) keeps the full
  archive; the posting path runs through the approval Sheet described below,
  where every row starts `pending` and only a manual edit to `approved`
  releases it.
- **One optional secret** — `MAKE_WEBHOOK_URL` (below). Otherwise it reuses
  whichever `LLM_PROVIDER` is already configured; the only `GITHUB_TOKEN` use
  is uploading the explainer video to the `social-videos` release.

### The explainer video: one HyperFrames template, new data every day

Every draft also gets a ~25-second silent animated explainer (4:5 portrait,
1080×1350 — LinkedIn's max feed footprint): hook line → the problem with the
real report/people counts animating in → the recurring broken loop → the
proposed fix revealing step by step → the validation question + disclosure.
The template lives in `video/` (`index.html`, a [HyperFrames](https://github.com/heygen-com/hyperframes)
composition — plain HTML/CSS/GSAP rendered to deterministic MP4); the
per-post data is a flat variables file built by `video.build_scene_script`
from the same LLM call that writes the post copy (`video_*` fields in
`SocialDraftCopy`), under the same rules: curator voice, no invented
experience, and **no LLM-written numbers on screen** — the counts are
injected from the Opportunity itself.

Operationally:

- `adapters/hyperframes_video.py` runs `npx hyperframes render` in `video/`
  and `gh release upload social-videos <mp4>`; the resulting
  `https://github.com/<repo>/releases/download/social-videos/<date>-<id>.mp4`
  URL rides the queue as `video_url`. (Swap-to-S3-later plan: only those two
  steps change; everything downstream is just a URL.)
- **One-time setup**: create the rolling release once —
  `gh release create social-videos --title "Social videos" --notes "MP4 assets for the daily social drafts"`.
- **The video is optional by design.** A render/upload failure logs loudly
  but the draft still queues with an empty `video_url`; Make.com posts those
  text-only. HyperFrames is pre-1.0 (version pinned exactly in
  `video/package.json` — bump deliberately, then re-render the fixture and
  eyeball it).
- The video renders **before** you polish in the Sheet, so its on-screen text
  is deliberately minimal. A video wrong enough to matter means flip the row
  to `skipped` — there's no editing a baked MP4.
- Preview/iterate locally in `video/`: `npx hyperframes preview`, and
  `npx hyperframes render --variables-file data/script.fixture.json --output out/fixture.mp4`
  (needs Node 22+ and FFmpeg; `npm install` first).
- Videos are LinkedIn-only; X stays text.

### The posting queue: Make.com + Google Sheet, approval in the Sheet

The pipeline's side is small: after writing `SOCIAL_DRAFTS.md`, the draft run
stores the exact publish-ready strings in the `social_queue` table and POSTs
them as JSON to the Make.com webhook in `MAKE_WEBHOOK_URL`:

```json
{
  "opportunity_id": "…",
  "date": "2026-07-15",
  "linkedin_post": "…full post incl. disclosure…",
  "x_thread": "…numbered tweets incl. link + disclosure…",
  "link": "https://reddit.com/…",
  "video_url": "…release asset URL, or empty when the render failed…"
}
```

A webhook failure fails the run *after* the draft and its `social_queue` row
are committed; re-send by hand with
`pain-point-pipeline social-approve --opportunity-id <id>` (`--force` to
re-send one that was already delivered — the id is in the draft's heading in
`SOCIAL_DRAFTS.md`).

The Google Sheet needs these columns (order matters to the scenarios below):

```
date | opportunity_id | linkedin_post | x_thread | link | video_url | status
```

`status` is the human approval gate: `pending` (as queued) → `approved`
(you edited/polished the text in the row and flipped it) → `posted` (set by
Make.com after publishing). Polish the copy **in the Sheet** — that's the
text that actually gets posted; `SOCIAL_DRAFTS.md` keeps only the unpolished
original. Part of the approval pass: click `video_url` and watch the MP4 —
approving the row approves the video.

**Format the `date` column as Date** (select the column → Format → Number →
Date) as part of one-time setup. The pipeline sends `date` as a plain
`YYYY-MM-DD` string; Make.com's "Add a Row" module (`USER_ENTERED` input mode)
converts recognized date strings to Sheets' internal date-serial number, same
as if you'd typed it by hand. Without the column formatted as Date, Sheets
displays that serial number raw (e.g. `46219`) instead of rendering it back
as a date — cosmetic only, the underlying value is correct, but confusing
until the column format is set once.

Two Make.com scenarios to build (in Make.com's UI — not configurable from
this repo):

1. **Intake**: Custom webhook (this is the URL for `MAKE_WEBHOOK_URL`) →
   Google Sheets "Add a Row" mapping the six payload fields, `status` =
   `pending`.
2. **Daily poster**: Schedule (daily) → Google Sheets "Search Rows" for the
   oldest row with `status = approved` (stop if none — a quiet day is
   normal) → **Router** with two branches:
   - *Has video* (filter: `video_url` is not empty): HTTP "Get a file" on
     `video_url` → LinkedIn "Create a Post" with Media Type **Video** and
     the downloaded file, content = `linkedin_post`.
   - *No video* (fallback): LinkedIn "Create a Post" with Media Type
     **None**, content = `linkedin_post`.
   Both branches then continue: LinkedIn comment with `link` if the comment
   module exists (else the reminder email with `link` + post URL — keeps the
   outbound link out of the main post, same reach logic as the draft format)
   → Google Sheets "Update a Row" setting `status = posted`.

Caveat before wiring scenario 2: posting to a member profile through
Make.com's LinkedIn connection requires LinkedIn OAuth consent including
`w_member_social`. Connect the LinkedIn account in Make.com and confirm a
test share works before trusting the daily schedule. X posting isn't wired
up — the `x_thread` column rides along so the approved, polished text is in
one place if you post it by hand (or automate it later).

## Testing before the first scheduled run

All three workflows also trigger on `workflow_dispatch`, so you can run any of them manually from the Actions tab once secrets are set, rather than waiting for the next cron firing.
