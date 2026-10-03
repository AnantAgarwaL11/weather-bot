"""Deterministic SOP matching: numeric thresholds are evaluated only in Python."""
from __future__ import annotations

import operator

from pydantic import BaseModel

from app.policies.loader import SOP, Condition, PolicySet
from app.weather.alerts import ALERT_LEVELS, AlertContext
from app.weather.models import METRICS, format_number

OPS = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le, "eq": operator.eq}
OP_TEXT = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "="}


class Evidence(BaseModel):
    metric: str
    op: str
    threshold: float | bool
    actual: float | bool
    unit: str

    def render(self) -> str:
        label, _ = METRICS[self.metric]
        if isinstance(self.actual, bool):
            return f"{label}: {'yes' if self.actual else 'no'}"
        unit = f" {self.unit}" if self.unit else ""
        return f"{label} {format_number(self.actual)}{unit} {OP_TEXT[self.op]} threshold {format_number(self.threshold)}{unit}"


class Match(BaseModel):
    sop_id: str
    evidence: list[Evidence]
    alert_event: str | None = None


class MatchOutcome(BaseModel):
    matches: list[Match]
    unevaluable: list[str]  # SOP ids skipped because a required metric was missing


def match_policies(
    policies: PolicySet,
    activity: str | None,
    groups: list[str],
    metrics: dict[str, float | bool | None],
    alerts: AlertContext,
) -> MatchOutcome:
    matches, unevaluable = [], []
    if not activity:
        return MatchOutcome(matches=[], unevaluable=[])
    for sop in policies.policies:
        if not _scope_applies(sop, activity, groups):
            continue
        result = _evaluate(sop, metrics, alerts)
        if result == "unevaluable":
            unevaluable.append(sop.id)
        elif result is not None:
            matches.append(result)
    return MatchOutcome(matches=matches, unevaluable=unevaluable)


def _scope_applies(sop: SOP, activity: str, groups: list[str]) -> bool:
    acts = sop.conditions.activities
    if acts != "any" and activity not in acts:
        return False
    return not sop.conditions.groups or bool(set(sop.conditions.groups) & set(groups))


def _check(cond: Condition, metrics: dict) -> Evidence | None | str:
    actual = metrics.get(cond.metric)
    if actual is None:
        return "missing"
    if OPS[cond.op](actual, cond.value):
        return Evidence(metric=cond.metric, op=cond.op, threshold=cond.value, actual=actual, unit=METRICS[cond.metric][1])
    return None


def _evaluate(sop: SOP, metrics: dict, alerts: AlertContext) -> Match | None | str:
    c = sop.conditions
    alert_event = None
    if c.alert:
        floor = ALERT_LEVELS.index(c.alert.min_level)
        hit = next((a for a in alerts.alerts if ALERT_LEVELS.index(a.level) >= floor), None)
        if not alerts.provider_configured or hit is None:
            return None  # never assume an official alert without a verified source
        alert_event = f"{hit.event} ({hit.level}, source: {hit.source})"

    evidence: list[Evidence] = []
    for cond in c.all:
        r = _check(cond, metrics)
        if r == "missing":
            return "unevaluable"
        if r is None:
            return None
        evidence.append(r)
    if c.any:
        results = [_check(cond, metrics) for cond in c.any]
        hits = [r for r in results if isinstance(r, Evidence)]
        if not hits:
            return "unevaluable" if "missing" in results else None
        evidence.extend(hits)
    return Match(sop_id=sop.id, evidence=evidence, alert_event=alert_event)
