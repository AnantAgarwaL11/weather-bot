"""LIVE integration tests against the real Open-Meteo APIs.

Opt-in: `RUN_LIVE=1 pytest -m live`. Weather changes constantly, so these tests
assert *consistency* (the bot's decision equals an independent re-evaluation of the
values it actually retrieved) rather than a particular outcome. The severe test
probes climatically extreme places and skips honestly if none is severe right now;
the deterministic severe test lives in test_graph.py.
"""
from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.policies.matcher import match_policies
from app.policies.resolver import resolve
from app.services.chat import ChatService, build_deps
from app.weather.alerts import AlertContext

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run live API tests")]


@pytest.fixture(scope="module")
def live():
    settings = get_settings()
    deps = build_deps(settings)
    deps.llm, deps.rephrase = None, False  # live tests verify APIs + engine, not the LLM
    deps.interpreter.llm = None
    return ChatService(deps)


def _independent_selection(svc, activity, groups, result):
    metrics = {f.metric: f.value for f in result.weather_facts}
    out = match_policies(svc.deps.store.policies, activity, groups, metrics, AlertContext(provider_configured=False))
    return resolve(svc.deps.store.policies, out.matches).selected.sop_id if out.matches else None


def test_live_geocoding(live):
    loc = live.deps.geocoder.resolve("Bhopal")
    assert loc.country == "India" and abs(loc.latitude - 23.25) < 0.5


def test_live_weather_and_consistent_decision(live):
    r = live.chat("live-1", "Is it safe to cycle in Bhopal today?")
    assert r.outcome in ("answered", "no_matching_sop", "validation_failed")
    assert r.weather_facts, "live weather facts should be present"
    assert r.period["timezone"] == "Asia/Kolkata"
    assert r.selected_sop == _independent_selection(live, "cycling", [], r) or r.outcome != "answered"
    if r.outcome == "answered":
        for f in r.weather_facts:
            assert f.render().split(": ", 1)[1] in r.response  # actual retrieved values quoted


def test_live_followup_fetches_new_period(live):
    live.chat("live-2", "Is it safe to cycle in Bhopal today?")
    r = live.chat("live-2", "What about tomorrow morning?")
    assert r.activity == "cycling" and r.location.startswith("Bhopal")
    assert r.time_window == "tomorrow_morning" and "T06:00" in r.period["start"]


@pytest.mark.parametrize("city", ["Yakutsk", "Norilsk", "Oymyakon", "Longyearbyen"])
def test_live_severe_weather(live, city):
    """If live conditions at an extreme location meet a high/critical SOP, it must be selected
    and the actual retrieved values must be quoted; otherwise skip (no fabricated severity)."""
    r = live.chat(f"live-sev-{city}", f"Can I go for a walk in {city} today?")
    if r.outcome == "weather_unavailable":
        pytest.fail(f"live weather retrieval failed for {city}: {r.response}")
    expected = _independent_selection(live, "walking", [], r)
    sop = live.deps.store.policies.get(expected) if expected else None
    if sop is None or sop.severity not in ("high", "critical"):
        pytest.skip(f"{city}: no high-severity conditions live right now ({expected})")
    assert r.selected_sop == expected and r.severity in ("high", "critical")
    assert expected in r.response
    for ev_metric in sop.metrics:
        fact = next((f for f in r.weather_facts if f.metric == ev_metric), None)
        if fact is not None:
            assert fact.render().split(": ", 1)[1] in r.response
