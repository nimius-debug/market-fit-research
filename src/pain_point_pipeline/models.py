"""Domain types for the pain-point discovery pipeline. See CONTEXT.md for the glossary these mirror."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

EffortSize = Literal["S", "M", "L", "XL"]
IssueStatus = Literal["open", "rejected"]


@dataclass(frozen=True)
class RawItem:
    """A single post or comment as fetched from a Source, before classification."""

    id: str
    source: str
    external_id: str
    author: str
    url: str
    text: str
    created_at: datetime  # naive, implicitly UTC — every SourcePort adapter must follow this convention


@dataclass(frozen=True)
class PainPoint:
    """A RawItem the LLM judged to express genuine frustration or an unmet need."""

    id: str
    raw_item: RawItem
    summary: str
    created_at: datetime


@dataclass
class Opportunity:
    """A cluster of related Pain Points suggesting the same recurring underlying problem."""

    id: str
    title: str
    pain_points: list[PainPoint] = field(default_factory=list)
    solvable: bool | None = None
    solvable_rationale: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def frequency(self) -> int:
        return len(self.pain_points)

    @property
    def distinct_authors(self) -> int:
        return len({pp.raw_item.author for pp in self.pain_points})


@dataclass(frozen=True)
class OpportunitySummary:
    """The minimal shape an LLM needs to decide whether a new Pain Point matches an existing Opportunity."""

    id: str
    title: str


@dataclass(frozen=True)
class OpportunityBrief:
    """The report generated for a single Opportunity, given to the user for a build/no-build decision."""

    opportunity_id: str
    problem_summary: str
    solution_sketch: str
    effort_size: EffortSize
    effort_rationale: str
    competitor_check: str
    generated_at: datetime


@dataclass(frozen=True)
class OpportunityIssue:
    """The GitHub Issue tracking an Opportunity, and its last-known Rejected status."""

    opportunity_id: str
    issue_number: int
    status: IssueStatus
    checked_at: datetime
