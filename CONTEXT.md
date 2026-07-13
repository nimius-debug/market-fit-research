# Pain-Point Discovery System

Finds pain points expressed in online communities within niches the user has real expertise in, and turns them into opportunity briefs for the user to evaluate. It does not build software automatically (see [ADR-0001](./docs/adr/0001-discovery-tool-not-autonomous-builder.md)).

## Language

**Niche**:
An industry or interest area the user has enough first-hand expertise in to judge whether a surfaced pain point is real and worth solving. Candidate niches: software engineering tooling, AI/automation tooling, Roblox/game dev coaching, construction, electrical trade, daycare/childcare operations.

**Target Niche (v1)**:
AI and automation (LLM apps, agents, no-code/workflow automation). Replaces the original Roblox/game-dev v1 niche — Roblox stayed blocked on Reddit's Responsible Builder Policy approval (see docs/deployment.md), and AI/automation is both Reddit-native and squarely inside the user's own software expertise, so a surfaced pain point can move quickly from brief to prototype.

**Source**:
A platform the system reads from to find pain points. v1 source: Reddit (r/AI_Agents, r/automation, r/artificial, r/nocode, r/SaaS, r/LocalLLaMA — see `DEFAULT_SUBREDDITS` in `adapters/reddit.py` and `adapters/arctic_shift.py`), read via ArcticShiftSource (a free, no-auth, community-run API, unblocked immediately) with RedditSource (the official API) as the eventual replacement once Reddit's approval lands — see docs/deployment.md. The Roblox DevForum source used in the original v1 niche has no AI/automation equivalent and is no longer wired into the pipeline (`adapters/devforum.py` is kept but unused, in case a Roblox-adjacent niche is added later). Discord is explicitly excluded from v1 due to access-permission and signal-noise concerns.

**Pain Point**:
A single post or comment, judged by an LLM classifier (not keyword matching), that expresses genuine frustration, an unmet need, or a workaround for a problem. Not every complaint qualifies — venting with no underlying unmet need is not a Pain Point.
_Avoid_: Complaint, issue, problem (too generic; use Pain Point for the classified, structured concept).

**Opportunity**:
A cluster of related Pain Points that **one well-scoped tool could plausibly address** — that is the clustering criterion, not surface similarity of wording. The unit the system ultimately ranks and reports on — not the individual Pain Point. Only Opportunities judged **Solvable** are surfaced; ranking among those is primarily by frequency (distinct Pain Points/users). Matching compares the new Pain Point's one-sentence summary against a bounded candidate list (the ~150 most recently touched Opportunities plus the top ~50 by Pain Point count, so heavy recurring problems never age out of candidacy).

**Solvable**:
An Opportunity is Solvable if the LLM judges the underlying problem to be addressable by a piece of software a solo developer could plausibly build — as opposed to problems only the platform owner (e.g. Roblox Corp) could fix. A hard filter, not a ranking weight: unsolvable Opportunities are excluded, not just ranked lower.

**Opportunity Brief**:
The report the system produces for a single Opportunity, given to the user for a build/no-build decision. Contains: problem summary, evidence (representative quotes/links), frequency signal, solution sketch, solvability rationale, competitor check, and solo-dev effort estimate.

**Digest**:
The weekly markdown file the user actually reads. Each week's section lists the Opportunity Briefs that are new or meaningfully updated since the last Digest. **Newest section first** — new weeks are prepended right under the title, not appended to the end, so the top of the file is always what's current (nothing is ever deleted, so it's append-*safe*, just not literally append-only). Brief text (problem, fix idea, effort, competitor check) is written in short, plain sentences on purpose — a 10-year-old's reading level, not a business memo — since this file is meant to be scanned quickly. The ingestion pipeline currently runs on the same weekly cadence (see docs/deployment.md).

**Rejected**:
A status the user can apply to an Opportunity to suppress it from future Digests. Applied by closing (or labeling) the GitHub Issue auto-created for that Opportunity. Issues and Opportunity Briefs are only generated once an Opportunity has **3+ distinct authors** (multiple people hit the problem — one person posting repeatedly never qualifies); below the gate an Opportunity is still clustered and judged Solvable, and its brief + Issue arrive automatically when the third voice joins. Issue titles carry the live counts — e.g. `… (13 reports, 5 people)` — refreshed as the Opportunity grows. The system does not otherwise learn from rejections in v1 — no ranking bias, just suppression.
