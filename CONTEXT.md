# Pain-Point Discovery System

Finds pain points expressed in online communities within niches the user has real expertise in, and turns them into opportunity briefs for the user to evaluate. It does not build software automatically (see [ADR-0001](./docs/adr/0001-discovery-tool-not-autonomous-builder.md)).

## Language

**Niche**:
An industry or interest area the user has enough first-hand expertise in to judge whether a surfaced pain point is real and worth solving. Candidate niches: software engineering tooling, Roblox/game dev coaching, construction, electrical trade, daycare/childcare operations.

**Target Niche (v1)**:
Roblox / game dev coaching. Chosen as the v1 niche because it is Reddit/forum-native (easiest data access) and closest to the user's own software skill set, so a surfaced pain point can move quickly from brief to prototype.

**Source**:
A platform the system reads from to find pain points. v1 sources: Reddit (r/robloxgamedev, r/roblox_dev, r/RobloxDevelopers) and the Roblox DevForum (devforum.roblox.com). Discord is explicitly excluded from v1 due to access-permission and signal-noise concerns.

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
The weekly, append-only markdown file the user actually reads. Each week's section lists the Opportunity Briefs that are new or meaningfully updated since the last Digest. The ingestion pipeline itself still runs daily; the Digest is just the weekly reading cadence layered on top.

**Rejected**:
A status the user can apply to an Opportunity to suppress it from future Digests. Applied by closing (or labeling) the GitHub Issue auto-created for that Opportunity. The system does not otherwise learn from rejections in v1 — no ranking bias, just suppression.
