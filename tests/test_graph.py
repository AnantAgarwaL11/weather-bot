"""Graph branch tests: severe weather (simulated fixture), no-SOP, clarification,
location failures. Fixture weather is SIMULATED, not a live event."""
from __future__ import annotations

import json

from fakes import FIXTURES, make_forecast, make_service
from app.weather.models import format_number


def _storm():
    return json.loads((FIXTURES / "simulated_severe_storm.json").read_text(encoding="utf-8"))


# Test 4 (deterministic): severe weather from a simulated storm fixture
def test_simulated_severe_storm_selects_critical_policy(api, store):
    api.forecast = _storm()
    r = make_service(api, store).chat("s", "Is it safe to cycle in Bhopal this afternoon?")
    assert r.outcome == "answered"
    assert r.selected_sop == "HAZ-001" and r.severity == "critical"
    facts = {f.metric: f.value for f in r.weather_facts}
    assert facts["thunderstorm"] is True and facts["wind_gust_max"] == 74.0
    assert "74 km/h" in r.response and "90 %" in r.response  # actual fixture values
    assert "HAZ-001 — Thunderstorm activity forecast" in r.response


def test_storm_like_rain_and_wind_without_thunder(api, store):
    """Rain 75% + gusts 55 km/h, no thunder: combined-risk HAZ-003 wins for every activity."""
    api.forecast = make_forecast(overrides={"ALL": {"precipitation_probability": 75, "wind_gusts_10m": 55.0}})
    svc = make_service(api, store)
    for i, q in enumerate(["Can I drive in Bhopal today?", "Is today good for a picnic in Bhopal?",
                           "Can I go for a walk in Bhopal today?"]):
        assert svc.chat(f"x{i}", q).selected_sop == "HAZ-003"


# Test 5: no applicable SOP
def test_out_of_scope_activity(api, store):
    r = make_service(api, store).chat("o", "Is it safe to fly a drone in Bhopal today?")
    assert r.outcome == "out_of_scope"
    assert r.sop_ids == [] and r.selected_sop is None
    assert "don't have an SOP" in r.response
    assert api.weather_calls() == 0  # no weather fetched for unsupported requests


def test_supported_activity_but_no_sop_for_conditions(api, store):
    """Cycling at 45% rain chance falls between CYC-003 (<30) and CYC-001 (>=60): no SOP applies."""
    api.forecast = make_forecast(overrides={"ALL": {"precipitation_probability": 45}})
    r = make_service(api, store).chat("g", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "no_matching_sop"
    assert r.sop_ids == []
    assert "No SOP applies" in r.response
    for verdict in ("Yes —", "Not recommended", "avoid"):
        assert verdict not in r.response


def test_missing_location_asks_for_clarification(api, store):
    svc = make_service(api, store)
    r = svc.chat("c", "Is tomorrow a good day for a picnic?")
    assert r.outcome == "clarification_needed" and "Which city" in r.response
    r2 = svc.chat("c", "Bhopal")
    assert r2.outcome == "answered" and r2.time_window == "tomorrow" and r2.activity == "picnic"


def test_unknown_city(api, store):
    r = make_service(api, store).chat("u", "Can I cycle in Atlantisville today?")
    assert r.outcome == "location_not_found" and "couldn't find" in r.response
    assert api.weather_calls() == 0


def test_ambiguous_city(api, store):
    svc = make_service(api, store)
    r = svc.chat("a", "Can I go for a run in Springfield today?")
    assert r.outcome == "location_ambiguous" and "Illinois" in r.response and "Missouri" in r.response


def test_past_period_is_not_assessed(api, store):
    api.forecast = make_forecast(now="2026-10-03T19:00")
    r = make_service(api, store).chat("p", "Can I cycle in Bhopal this morning?")
    assert r.outcome == "period_unavailable" and r.sop_ids == []


def test_unsupported_period(api, store):
    r = make_service(api, store).chat("w", "Can I cycle in Bhopal next week?")
    assert r.outcome == "period_unavailable" and api.weather_calls() == 0


def test_format_number():
    assert format_number(74.0) == "74" and format_number(12.34) == "12.34"
