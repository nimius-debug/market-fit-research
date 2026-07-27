"""Natural-language framing for an Opportunity's frequency/people counts.

The Digest, Social Draft header, GitHub Issue title, and explainer video all
need to say "N people, M posts" in some form. A single fixed template reads
like a form letter repeated across every entry; this module varies the verb
deterministically per Opportunity (keyed by its id, not randomly re-rolled
every render) so the wording feels natural without becoming non-reproducible
— the same Opportunity always picks the same verb, so issue-title refreshes
and repeated digest/video builds stay stable and testable.
"""

from __future__ import annotations

import hashlib

VERBS = (
    "dealing with",
    "stuck on",
    "venting about",
    "arguing about",
    "struggling with",
    "frustrated by",
    "still fighting",
    "complaining about",
)

REPORT_TAILS = (
    "posts about it",
    "posts and counting",
    "posts on this exact problem",
    "posts saying the same thing",
    "posts, same complaint every time",
)


def _pick(key: str, pool: tuple[str, ...]) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()
    return pool[int(digest, 16) % len(pool)]


def verb_for(opportunity_id: str) -> str:
    """The verb phrase for this Opportunity, e.g. 'stuck on' or 'venting about'."""
    return _pick(opportunity_id, VERBS)


def report_tail_for(opportunity_id: str) -> str:
    """The trailing phrase for a bare posts-count line, e.g. 'posts and counting'."""
    return _pick(opportunity_id + ":reports", REPORT_TAILS)


def count_sentence(opportunity_id: str, frequency: int, distinct_authors: int) -> str:
    """Prose line for the Digest / Social Draft header, e.g.
    '9 people on Reddit are stuck on this — 12 posts'."""
    verb = verb_for(opportunity_id)
    return f"{distinct_authors} people on Reddit are {verb} this — {frequency} posts"


def issue_title_suffix(opportunity_id: str, frequency: int, distinct_authors: int) -> str:
    """Parenthetical for the GitHub Issue title, e.g.
    '(12 posts, 9 people on Reddit stuck on it)'."""
    verb = verb_for(opportunity_id)
    return f"({frequency} posts, {distinct_authors} people on Reddit {verb} it)"
