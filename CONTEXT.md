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
A cluster of related Pain Points, grouped automatically by semantic similarity, that together suggest the same recurring underlying problem across multiple posts/users. The unit the system ultimately ranks and reports on — not the individual Pain Point. Only Opportunities judged **Solvable** are surfaced; ranking among those is primarily by frequency (distinct Pain Points/users).

**Solvable**:
An Opportunity is Solvable if the LLM judges the underlying problem to be addressable by a piece of software a solo developer could plausibly build — as opposed to problems only the platform owner (e.g. Roblox Corp) could fix. A hard filter, not a ranking weight: unsolvable Opportunities are excluded, not just ranked lower.

**Opportunity Brief**:
The report the system produces for a single Opportunity, given to the user for a build/no-build decision. Contains: problem summary, evidence (representative quotes/links), frequency signal, solution sketch, solvability rationale, competitor check, and solo-dev effort estimate.

**Digest**:
The weekly, append-only markdown file the user actually reads. Each week's section lists the Opportunity Briefs that are new or meaningfully updated since the last Digest. The ingestion pipeline currently runs on the same weekly cadence (see docs/deployment.md).

**Rejected**:
A status the user can apply to an Opportunity to suppress it from future Digests. Applied by closing (or labeling) the GitHub Issue auto-created for that Opportunity. Issues are only auto-created once an Opportunity has **2+ Pain Points** (a recurring problem, not a single complaint) — solvable singletons still get judged and briefed, and their Issue opens automatically if a second Pain Point later joins. The system does not otherwise learn from rejections in v1 — no ranking bias, just suppression.
