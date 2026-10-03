"""Conflict resolution between multiple matched SOPs.

Strategy (documented in README): exactly ONE SOP drives the recommendation.
  1. highest severity (critical > high > moderate > low > info)
  2. higher `priority`
  3. more specific scope (group-scoped > activity-scoped > any-activity)
  4. SOP id (stable, deterministic)
Other matches are listed for traceability only; their recommendations are never
merged, which avoids contradictory advice ("ok" vs "avoid").
"""
from __future__ import annotations

from pydantic import BaseModel

from app.policies.loader import SOP, PolicySet
from app.policies.matcher import Match


class Resolution(BaseModel):
    selected: Match
    others: list[str]
    rationale: str


def _specificity(sop: SOP) -> int:
    return (2 if sop.conditions.groups else 0) + (1 if sop.conditions.activities != "any" else 0)


def resolve(policies: PolicySet, matches: list[Match]) -> Resolution:
    if not matches:
        raise ValueError("resolve() requires at least one match")
    sops = {m.sop_id: policies.get(m.sop_id) for m in matches}
    ranked = sorted(
        matches,
        key=lambda m: (-sops[m.sop_id].severity_rank, -sops[m.sop_id].priority, -_specificity(sops[m.sop_id]), m.sop_id),
    )
    top = sops[ranked[0].sop_id]
    others = [m.sop_id for m in ranked[1:]]
    if not others:
        rationale = f"{top.id} was the only matching SOP."
    else:
        rationale = (
            f"{len(matches)} SOPs matched; {top.id} selected as highest severity ({top.severity}, "
            f"priority {top.priority}). Lower-precedence matches: {', '.join(others)}."
        )
    return Resolution(selected=ranked[0], others=others, rationale=rationale)
