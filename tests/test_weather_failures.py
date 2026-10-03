"""Test 6: unreachable / failing weather API takes the failure branch with no advice."""
from __future__ import annotations

import httpx
import pytest

from fakes import make_forecast, make_service

ADVICE_MARKERS = ["Yes —", "Not recommended", "No — avoid", "Possible, with precautions", "Guidance from"]


@pytest.mark.parametrize("error,reason", [
    (httpx.ConnectTimeout("timed out"), "timed out"),
    (httpx.ConnectError("unreachable"), "could not be reached"),
    (500, "could not be reached"),
    (503, "could not be reached"),
    (429, "rate limit"),
])
def test_weather_api_failure(api, store, error, reason):
    api.forecast_error = error
    r = make_service(api, store).chat("f", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "weather_unavailable"
    assert r.sop_ids == [] and r.weather_facts == []
    assert "couldn't retrieve live weather" in r.response and reason in r.response
    assert not any(m in r.response for m in ADVICE_MARKERS)


def test_missing_required_field_is_not_fabricated(api, store):
    api.forecast = make_forecast(drop_fields=("uv_index",))
    r = make_service(api, store).chat("m", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "weather_unavailable" and "incomplete data" in r.response


def test_invalid_json(api, store):
    api.forecast = None  # serialises to JSON null -> no hourly block
    r = make_service(api, store).chat("j", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "weather_unavailable"


def test_null_values_are_skipped_not_invented(api, store):
    """Hourly nulls for UV across the window -> metric absent, SOPs needing it are unevaluable."""
    data = make_forecast()
    data["hourly"]["uv_index"] = [None] * len(data["hourly"]["uv_index"])
    api.forecast = data
    r = make_service(api, store).chat("n", "Can I take my kids to the park in Bhopal today?")
    assert "uv_index_max" not in {f.metric for f in r.weather_facts}
    # CHD-001 (high) cannot be ruled out without UV data, so the milder PIC-002 "ok" is NOT issued.
    assert r.outcome == "validation_failed" and r.sop_ids == []
    assert "CHD-001" in r.response and "won't give a recommendation" in r.response


def test_missing_metric_for_lower_severity_sop_still_answers(api, store):
    """Missing UV only blocks answers when an unevaluable SOP outranks the selected one."""
    data = make_forecast(overrides={"ALL": {"weather_code": 95}})
    data["hourly"]["uv_index"] = [None] * len(data["hourly"]["uv_index"])
    api.forecast = data
    r = make_service(api, store).chat("n2", "Can I go for a walk in Bhopal today?")
    assert r.selected_sop == "HAZ-001"
    assert any("HAZ-006" in item for item in r.limitations)


def test_geocoding_service_down(api, store):
    api.geo_error = httpx.ConnectError("down")
    r = make_service(api, store).chat("g", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "weather_unavailable" and "location service" in r.response
    assert api.weather_calls() == 0
