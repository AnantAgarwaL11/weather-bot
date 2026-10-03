"""LangGraph nodes. Each node has one responsibility and returns a partial state update.
External services are injected via `Deps` so tests can swap in fakes/mock transports."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.agent.interpreter import Intent, Interpreter
from app.agent.llm import LLMClient
from app.agent.prompts import REPHRASE_SYSTEM_PROMPT
from app.agent.response import compose_answer, failure_message, grounding_violations, standard_limitations
from app.agent.state import AgentState
from app.policies.loader import PolicyStore
from app.policies.matcher import Match, match_policies
from app.policies.resolver import Resolution, resolve
from app.weather.alerts import AlertContext, AlertProvider
from app.weather.client import WeatherClient
from app.weather.geocoding import AmbiguousLocation, Geocoder, LocationNotFound
from app.weather.models import Location, WeatherError, WeatherReport

log = logging.getLogger(__name__)

WEATHER_REASONS = {
    "weather_timeout": "the weather service timed out",
    "weather_unavailable": "the weather service could not be reached",
    "weather_rate_limited": "the weather service rate limit was reached",
    "weather_invalid": "the weather service returned incomplete data",
    "geocoding_unavailable": "the location service could not be reached",
    "geocoding_invalid": "the location service returned incomplete data",
}


@dataclass
class Deps:
    store: PolicyStore
    interpreter: Interpreter
    geocoder: Geocoder
    weather: WeatherClient
    alerts: AlertProvider
    llm: LLMClient | None = None
    rephrase: bool = False


class Nodes:
    def __init__(self, deps: Deps):
        self.d = deps

    # 1. Understand the query (rules + optional constrained LLM)
    def understand_query(self, state: AgentState) -> dict:
        intent = self.d.interpreter.interpret(state["user_message"], state.get("context") or {})
        if intent.injection_suspected:
            log.warning("possible prompt injection detected; SOP engine unaffected")
        return {"intent": intent.model_dump()}

    # 2. Merge with session memory and detect missing details
    def resolve_context(self, state: AgentState) -> dict:
        ctx = dict(state.get("context") or {})
        intent = Intent(**state["intent"])
        pending = ctx.pop("pending", None)
        loc_q = intent.location
        if not loc_q and pending == "location":
            loc_q = _bare_location(state["user_message"])
        elif not loc_q and not intent.activity and ctx.get("activity") and _is_place_name(state["user_message"]):
            loc_q, intent.followup = _bare_location(state["user_message"]), True  # e.g. "Indore?"
        followup = intent.followup or pending is not None
        activity = intent.activity or (ctx.get("activity") if followup else None)
        groups = intent.groups or (ctx.get("groups", []) if followup else [])
        time_window = intent.time_window or (ctx.get("time_window") if followup else None) or "today"
        if loc_q and loc_q != ctx.get("location_query"):
            ctx["location"] = None  # new place: must be geocoded again
            ctx["location_query"] = loc_q
        policies = self.d.store.policies

        if not activity:
            if intent.followup and not ctx.get("activity") and not intent.injection_suspected:
                return {"context": ctx | {"pending": "activity"}, "outcome": "clarification_needed",
                        "detail": {"missing": "activity"}}
            supported = ", ".join(v.label for v in policies.activities.values())
            return {"context": ctx, "outcome": "out_of_scope", "detail": {"supported": supported}}
        ctx.update(activity=activity, groups=groups, time_window=time_window)
        if time_window == "unsupported":
            ctx["time_window"] = "today"
            return {"context": ctx, "outcome": "period_unavailable", "detail": {}}
        if not ctx.get("location_query"):
            return {"context": ctx | {"pending": "location"}, "outcome": "clarification_needed",
                    "detail": {"missing": "location", "activity_label": policies.activities[activity].label}}
        return {"context": ctx}

    # 3. Geocode (cached per session while the place name is unchanged)
    def resolve_location(self, state: AgentState) -> dict:
        ctx = dict(state["context"])
        query = ctx["location_query"]
        if ctx.get("location"):
            return {"location": ctx["location"]}
        try:
            loc = self.d.geocoder.resolve(query)
        except AmbiguousLocation as exc:
            ctx.update(location_query=None, pending="location")
            return {"context": ctx, "outcome": "location_ambiguous",
                    "detail": {"query": query, "options": [o.display for o in exc.options]}}
        except LocationNotFound:
            ctx.update(location_query=None, pending="location")
            return {"context": ctx, "outcome": "location_not_found", "detail": {"query": query}}
        except WeatherError as exc:
            return {"outcome": "weather_unavailable", "detail": {"reason": WEATHER_REASONS.get(exc.kind, exc.kind)}}
        ctx["location"] = loc.model_dump()
        return {"context": ctx, "location": ctx["location"]}

    # 4. Always fetch fresh weather for the requested window (never reuse old values)
    def fetch_weather(self, state: AgentState) -> dict:
        loc = Location(**state["location"])
        try:
            report = self.d.weather.get_report(loc, state["context"]["time_window"])
        except WeatherError as exc:
            if exc.kind == "period_unavailable":
                return {"outcome": "period_unavailable", "detail": {}}
            log.warning("weather failure: %s (%s)", exc.kind, exc.detail)
            return {"outcome": "weather_unavailable", "detail": {"reason": WEATHER_REASONS.get(exc.kind, exc.kind)}}
        try:
            alerts = self.d.alerts.get_alerts(loc)
        except Exception as exc:  # alert feed problems must not create or suppress advice silently
            log.warning("alert provider failed: %s", exc)
            alerts = AlertContext(provider_configured=False)
        return {"weather": report.model_dump(), "alerts": alerts.model_dump()}

    # 5. Load current SOPs and evaluate conditions deterministically
    def match_sops(self, state: AgentState) -> dict:
        ctx = state["context"]
        report = WeatherReport(**state["weather"])
        result = match_policies(self.d.store.policies, ctx["activity"], ctx.get("groups", []),
                                report.metrics, AlertContext(**state["alerts"]))
        out = {"matches": [m.model_dump() for m in result.matches], "unevaluable": result.unevaluable}
        if not result.matches:
            out |= {"outcome": "no_matching_sop", "detail": {
                "location": report.location.display, "period": report.period_label,
                "activity_label": self.d.store.policies.activities[ctx["activity"]].label}}
        return out

    # 6. Conflict resolution: one SOP drives the answer
    def resolve_policy(self, state: AgentState) -> dict:
        res = resolve(self.d.store.policies, [Match(**m) for m in state["matches"]])
        return {"resolution": res.model_dump()}

    # 7. Verify the decision is fully supported by policy + weather evidence
    def validate_decision(self, state: AgentState) -> dict:
        problems = []
        res = Resolution(**state["resolution"])
        sop = self.d.store.policies.get(res.selected.sop_id)
        report = WeatherReport(**state["weather"])
        if sop is None:
            problems.append("selected SOP no longer exists")
        else:
            acts = sop.conditions.activities
            if acts != "any" and state["context"]["activity"] not in acts:
                problems.append("SOP scope does not cover the activity")
            if sop.conditions.alert and not res.selected.alert_event:
                problems.append("alert SOP selected without verified alert")
        for ev in res.selected.evidence:
            if report.metrics.get(ev.metric) != ev.actual:
                problems.append(f"evidence for {ev.metric} does not match weather data")
        if sop is not None:
            # Missing data must not let a milder SOP win over one we could not evaluate.
            blocked = [i for i in state.get("unevaluable", [])
                       if (u := self.d.store.policies.get(i)) and u.severity_rank > sop.severity_rank]
            if blocked:
                problems.append(f"insufficient weather data to rule out {', '.join(blocked)}")
        if not res.selected.evidence and not res.selected.alert_event:
            problems.append("no evidence recorded")
        if report.hours < 1:
            problems.append("empty forecast window")
        if problems:
            log.error("decision validation failed: %s", problems)
            return {"outcome": "validation_failed", "detail": {"problems": problems}}
        return {}

    # 8. Compose the grounded answer (Python), optionally reworded by the LLM and re-validated
    def generate_response(self, state: AgentState) -> dict:
        policies = self.d.store.policies
        ctx = state["context"]
        res = Resolution(**state["resolution"])
        sop = policies.get(res.selected.sop_id)
        report = WeatherReport(**state["weather"])
        alerts = AlertContext(**state["alerts"])
        intent = Intent(**state["intent"])
        limitations = standard_limitations(report, alerts.provider_configured, state.get("unevaluable", []),
                                           intent.injection_suspected)
        draft = compose_answer(sop, res.selected.evidence, report, policies.activities[ctx["activity"]].label,
                               [policies.get(i) for i in res.others], limitations, res.selected.alert_event)
        text = self._rephrase(draft, sop) if (self.d.rephrase and self.d.llm) else draft
        new_ctx = ctx | {"last_sop_ids": [sop.id] + res.others, "last_outcome": "answered"}
        return {"response": text, "outcome": "answered", "context": new_ctx, "limitations": limitations,
                "messages": [{"role": "assistant", "content": text}]}

    def _rephrase(self, draft: str, sop) -> str:
        try:
            text = self.d.llm.complete_text(REPHRASE_SYSTEM_PROMPT, draft)
        except Exception as exc:
            log.warning("rephrase failed, using deterministic draft: %s", exc)
            return draft
        problems = grounding_violations(text, draft, sop, self.d.store.policies)
        if problems:
            log.warning("rejected LLM rephrase: %s", problems)
            return draft
        return text

    # 9. Honest fallbacks for every non-answer branch
    def handle_failure(self, state: AgentState) -> dict:
        outcome = state.get("outcome") or "error"
        text = failure_message(outcome, state.get("detail") or {})
        ctx = dict(state.get("context") or {}) | {"last_outcome": outcome, "last_sop_ids": []}
        return {"response": text, "context": ctx, "messages": [{"role": "assistant", "content": text}]}


def _is_place_name(message: str) -> bool:
    words = re.sub(r"[^A-Za-z ,.'\-]", "", message).replace(",", " ").split()
    return 1 <= len(words) <= 3 and all(w[0].isupper() for w in words)


def _bare_location(message: str) -> str | None:
    """Treat a short reply to 'which city?' as the location (e.g. 'Bhopal' or 'Paris, France')."""
    cleaned = re.sub(r"[^A-Za-z ,.'\-]", "", message).strip(" .,")
    words = cleaned.replace(",", " ").split()
    if 1 <= len(words) <= 4:
        return cleaned.title() if cleaned.islower() else cleaned
    return None
