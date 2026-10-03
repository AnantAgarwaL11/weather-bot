"""Test 7: session memory, contextual follow-ups and session isolation."""
from __future__ import annotations

from fakes import make_forecast, make_service

# Dry morning, rainy evening (SIMULATED).
EVENING_RAIN = {f"{h:02d}": {"precipitation_probability": 85, "precipitation": 3.0} for h in range(17, 21)}


def test_followup_reuses_activity_and_location_with_new_window(api, store):
    api.forecast = make_forecast(now="2026-10-03T07:00", overrides=EVENING_RAIN)
    svc = make_service(api, store)
    r1 = svc.chat("m1", "Is it safe to cycle in Bhopal this morning?")
    assert r1.selected_sop == "CYC-003" and r1.period["start"].endswith("07:00")
    geo_calls = sum(1 for c in api.calls if c.url.host == "geo.test")

    r2 = svc.chat("m1", "What about this evening?")
    assert r2.activity == "cycling" and r2.location.startswith("Bhopal")
    assert r2.time_window == "this_evening"
    assert r2.period["start"].endswith("17:00") and r2.period["end"].endswith("20:00")
    assert r2.selected_sop == "CYC-001"  # evening rain evaluated, not stale morning data
    assert api.weather_calls() == 2  # fresh fetch for the new period
    assert sum(1 for c in api.calls if c.url.host == "geo.test") == geo_calls  # geocode cached


def test_followup_changes_location(api, store):
    svc = make_service(api, store)
    svc.chat("m2", "Is it safe to cycle in Bhopal today?")
    r = svc.chat("m2", "What about Indore?")
    assert r.location.startswith("Indore") and r.activity == "cycling"
    r = svc.chat("m2", "Bhopal?")
    assert r.location.startswith("Bhopal") and r.activity == "cycling"


def test_followup_adds_group(api, store):
    api.forecast = make_forecast(overrides={"ALL": {"apparent_temperature": 36.0}})
    svc = make_service(api, store)
    svc.chat("m3", "Is a picnic in Bhopal a good idea this afternoon?")
    r = svc.chat("m3", "And with my grandparents?")
    assert r.activity == "picnic" and r.selected_sop == "ELD-001"


def test_new_unrelated_question_does_not_inherit_activity(api, store):
    svc = make_service(api, store)
    svc.chat("m4", "Is it safe to cycle in Bhopal today?")
    r = svc.chat("m4", "Can I go paragliding tomorrow?")
    assert r.outcome == "out_of_scope"


def test_sessions_are_isolated(api, store):
    svc = make_service(api, store)
    svc.chat("alice", "Is it safe to cycle in Bhopal today?")
    r = svc.chat("bob", "What about this evening?")
    assert r.outcome == "clarification_needed"
    assert "Bhopal" not in r.response and r.location is None
    assert len(svc.history("alice")) == 2 and len(svc.history("bob")) == 2
