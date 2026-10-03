"""Test 10: exact weather grounding. Response numbers must come from the supplied
(SIMULATED) weather data, the selected SOP, or the period timestamps."""
from __future__ import annotations

import re

from fakes import make_forecast, make_service
from app.agent.response import grounding_violations
from app.weather.models import format_number

EXACT = {"temperature_2m": 31.4, "apparent_temperature": 38.2, "relative_humidity_2m": 47,
         "precipitation_probability": 20, "precipitation": 0.0, "wind_speed_10m": 12.3,
         "wind_gusts_10m": 22.6, "uv_index": 9.1, "weather_code": 1}


def _allowed_numbers(store, sop_ids, r) -> set[str]:
    allowed = {format_number(v) for v in EXACT.values()}
    for sid in sop_ids:
        sop = store.policies.get(sid)
        text = " ".join([sop.id, sop.response.summary, *sop.response.guidance])
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", text))
        allowed |= {format_number(c.value) for c in sop.conditions.all + sop.conditions.any if not isinstance(c.value, bool)}
    allowed |= set(re.findall(r"\d+", f"{r.period['start']} {r.period['end']} {r.current_conditions['time']}"))
    return {format_number(float(n)) for n in allowed}


def test_response_only_contains_supplied_values(api, store):
    api.forecast = make_forecast(now="2026-10-03T09:00", overrides={"ALL": EXACT})
    r = make_service(api, store).chat("g", "Is it ok to go for a run in Bhopal today?")
    assert r.selected_sop == "EXR-001"
    numbers = {format_number(float(n)) for n in re.findall(r"\d+(?:\.\d+)?", r.response)}
    extra = numbers - _allowed_numbers(store, r.sop_ids, r)
    assert not extra, f"fabricated numbers: {extra}"
    # exact values with correct units
    for expected in ("38.2 °C", "31.4 °C", "22.6 km/h", "12.3 km/h", "20 %", "Max UV index: 9.1", "47 %"):
        assert expected in r.response
    assert "°F" not in r.response and "mph" not in r.response
    # structured facts mirror the fixture exactly
    facts = {f.metric: (f.value, f.unit) for f in r.weather_facts}
    assert facts["apparent_temp_max"] == (38.2, "°C") and facts["wind_gust_max"] == (22.6, "km/h")
    assert "hourly forecast" in r.response and "Current conditions (model estimate" in r.response


class RephraseLLM:
    def __init__(self, text):
        self.text = text

    def complete_json(self, system, user):
        return {}

    def complete_text(self, system, user):
        return self.text(user)


def test_llm_rephrase_with_invented_number_is_rejected(api, store):
    api.forecast = make_forecast(overrides={"ALL": EXACT})
    bad = RephraseLLM(lambda draft: draft.replace("22.6 km/h", "45 km/h") + "\nAlso per SOP XYZ-999 you are fine.")
    r = make_service(api, store, llm=bad, rephrase=True).chat("r", "Is it ok to go for a run in Bhopal today?")
    assert "45 km/h" not in r.response and "XYZ-999" not in r.response
    assert "22.6 km/h" in r.response  # deterministic draft used instead


def test_llm_rephrase_that_stays_grounded_is_used(api, store):
    api.forecast = make_forecast(overrides={"ALL": EXACT})
    good = RephraseLLM(lambda draft: "Friendly version:\n" + draft)
    r = make_service(api, store, llm=good, rephrase=True).chat("r2", "Is it ok to go for a run in Bhopal today?")
    assert r.response.startswith("Friendly version:")


def test_grounding_checker_flags_problems(store):
    sop = store.policies.get("CYC-001")
    draft = "CYC-001: rain 80 %"
    assert grounding_violations("CYC-001: rain 80 %", draft, sop, store.policies) == []
    problems = grounding_violations("rain 95 % per ABC-123", draft, sop, store.policies)
    assert len(problems) == 3
