"""Deterministic response assembly and grounding validation.

Python builds every factual and policy-bearing sentence. The optional LLM may
only reword this draft, and its output is rejected unless it passes
`grounding_violations` (no new numbers, SOP id preserved, no invented SOP ids).
"""
from __future__ import annotations

import re

from app.policies.loader import SOP, PolicySet
from app.policies.matcher import Evidence
from app.weather.models import WeatherReport, format_number

VERDICT_TEXT = {
    "ok": "Yes — conditions are suitable.",
    "caution": "Possible, with precautions.",
    "not_recommended": "Not recommended.",
    "avoid": "No — avoid this for now.",
}
NO_ALERT_FEED = (
    "No verified official alert feed is configured, so government-issued warnings (e.g. cyclone or "
    "low-pressure alerts) are not reflected; only forecast data was evaluated."
)
CURRENT_LABELS = [("temperature_2m", "temperature", "°C"), ("apparent_temperature", "feels like", "°C"),
                  ("wind_speed_10m", "wind", "km/h"), ("wind_gusts_10m", "gusts", "km/h"),
                  ("precipitation", "precipitation", "mm")]


def compose_answer(
    sop: SOP, evidence: list[Evidence], report: WeatherReport, activity_label: str,
    others: list[SOP], limitations: list[str], alert_event: str | None = None,
) -> str:
    loc = report.location.display
    lines = [
        f"**{VERDICT_TEXT[sop.response.verdict]}** {sop.response.summary}",
        "",
        f"**Weather for {loc}, {report.period_label}** (hourly forecast {report.start} to {report.end} local time, "
        f"{report.timezone}; source: {report.source}):",
    ]
    lines += [f"- {f.render()}" for f in report.facts()]
    if report.current and report.period_key in ("now", "today"):
        parts = [f"{label} {format_number(report.current[k])} {unit}" for k, label, unit in CURRENT_LABELS
                 if report.current.get(k) is not None]
        if parts:
            lines.append(f"- Current conditions (model estimate at {report.current.get('time')}): {', '.join(parts)}")
    why = "; ".join(e.render() for e in evidence)
    if alert_event:
        why = f"verified alert: {alert_event}" + (f"; {why}" if why else "")
    lines += [
        "",
        f"**Why this applies:** for {activity_label}, {why}.",
        f"**Policy:** {sop.id} — {sop.name} (severity: {sop.severity})",
        f"**Guidance from {sop.id}:**",
    ]
    lines += [f"- {g}" for g in sop.response.guidance]
    if others:
        refs = "; ".join(f"{o.id} — {o.name} ({o.severity})" for o in others)
        lines.append(f"**Also matched (lower precedence, not combined):** {refs}")
    lines += ["", "**Limitations:**"] + [f"- {item}" for item in limitations]
    return "\n".join(lines)


def standard_limitations(report: WeatherReport, alert_configured: bool, unevaluable: list[str], injection: bool) -> list[str]:
    items = [f"Values are forecasts for {report.period_label} and may change; check again closer to the time."]
    if not alert_configured:
        items.append(NO_ALERT_FEED)
    if unevaluable:
        items.append(f"Could not evaluate {', '.join(unevaluable)} because required weather data was missing.")
    if injection:
        items.append("Requests to ignore or override the SOPs are not followed; only the configured SOPs are applied.")
    return items


NUM_RE = re.compile(r"(?<![\d\-])-?\d+(?:\.\d+)?")  # keeps the sign so "-5" and "5" differ
SOP_ID_RE = re.compile(r"\b[A-Z]{2,5}-\d{3}\b")


def _numbers(text: str) -> set[str]:
    return {format_number(float(n)) for n in NUM_RE.findall(text)}


def grounding_violations(text: str, draft: str, sop: SOP, policies: PolicySet) -> list[str]:
    """Check `text` only uses numbers present in the grounded draft and cites valid SOPs."""
    problems = []
    extra = _numbers(text) - _numbers(draft)
    if extra:
        problems.append(f"numbers not present in grounded data: {sorted(extra)}")
    if sop.id not in text:
        problems.append(f"selected SOP {sop.id} not cited")
    unknown = {i for i in SOP_ID_RE.findall(text) if policies.get(i) is None}
    if unknown:
        problems.append(f"unknown SOP ids cited: {sorted(unknown)}")
    return problems


# ---- failure / non-answer templates (deterministic) ----

def failure_message(outcome: str, detail: dict) -> str:
    if outcome == "clarification_needed":
        what = detail.get("missing")
        if what == "location":
            act = detail.get("activity_label")
            return (f"Which city or town should I check{' for ' + act if act else ''}? "
                    "I need a location to look up the live forecast.")
        return ("Which outdoor activity are you planning (for example cycling, a walk, a picnic, or road travel)? "
                "I can only advise on activities covered by my SOPs.")
    if outcome == "location_ambiguous":
        opts = "; ".join(detail.get("options", []))
        return f"I found several places called “{detail.get('query')}”: {opts}. Which one do you mean? (e.g. “City, Country”)"
    if outcome == "location_not_found":
        return (f"I couldn't find a place called “{detail.get('query')}”. Could you check the spelling or add the "
                "country (e.g. “Bhopal, India”)? I can't give a weather-based recommendation without a valid location.")
    if outcome == "weather_unavailable":
        return ("I couldn't retrieve live weather information right now"
                f" ({detail.get('reason', 'service unavailable')}). Without current data I can't give a "
                "weather-based safety recommendation. Please try again shortly.")
    if outcome == "period_unavailable":
        return ("I can only assess today (now, morning, afternoon, evening, tonight) and tomorrow, and not periods "
                "that have already passed. Could you pick one of those time windows?")
    if outcome == "out_of_scope":
        return ("I don't have an SOP covering that request, so I can't give guidance on it. I can help with "
                f"weather safety for: {detail.get('supported', 'supported outdoor activities')}.")
    if outcome == "no_matching_sop":
        return (f"I checked the forecast for {detail.get('location')} ({detail.get('period')}), but none of my SOPs "
                f"cover {detail.get('activity_label')} under these specific conditions, so I can't give a "
                "recommendation for this situation. No SOP applies.")
    if outcome == "validation_failed":
        reasons = "; ".join(detail.get("problems", []))
        return ("I couldn't fully verify a policy decision against the available weather data"
                f"{' (' + reasons + ')' if reasons else ''}, so I won't give a recommendation for this request.")
    return "Something went wrong while processing your request. No recommendation was made."
