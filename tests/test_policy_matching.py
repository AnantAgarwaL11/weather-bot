"""Policy engine tests: direct match, conflict resolution, paraphrases, invalid config,
and adding an SOP without code changes. All weather is SIMULATED."""
from __future__ import annotations

import shutil

import pytest
import yaml

from fakes import FIXTURES, POLICY_FILE, make_forecast, make_service
from app.policies.loader import PolicyLoadError, PolicyStore, load_policies
from app.policies.matcher import match_policies
from app.policies.resolver import resolve
from app.weather.alerts import AlertContext

RAINY_ALL_DAY = {"ALL": {"precipitation_probability": 80, "precipitation": 2.0}}


def test_policy_file_meets_coverage(store):
    """>= 12 SOPs, >= 4 categories, all severities represented."""
    ps = store.policies
    assert len(ps.policies) >= 12
    assert len({p.category for p in ps.policies}) >= 4
    assert {p.severity for p in ps.policies} == {"info", "low", "moderate", "high", "critical"}


# Test 1: direct SOP match
def test_direct_match_rain_cycling(api, store):
    """Cycling + 80% rain -> CYC-001 selected, cited, with its recommendation."""
    api.forecast = make_forecast(overrides=RAINY_ALL_DAY)
    r = make_service(api, store).chat("t1", "Is it safe to cycle in Bhopal today?")
    assert r.outcome == "answered"
    assert r.selected_sop == "CYC-001"
    assert "CYC-001" in r.response and "Two-wheeler riding in rain" in r.response
    assert store.policies.get("CYC-001").response.summary in r.response
    assert "Not recommended" in r.response


# Test 2: multiple applicable SOPs
def test_multiple_matches_highest_severity_wins(api, store):
    """Thunderstorm + rain + gusts while cycling: HAZ-001 (critical) beats CYC-001/CYC-002 (high)."""
    api.forecast = make_forecast(overrides={"ALL": {"weather_code": 95, "precipitation_probability": 85,
                                                    "wind_gusts_10m": 45.0}})
    r = make_service(api, store).chat("t2", "Should I ride my bike in Bhopal today?")
    assert r.selected_sop == "HAZ-001"
    assert set(r.sop_ids) >= {"HAZ-001", "CYC-001", "CYC-002"}
    # Only the selected SOP's recommendation and guidance appear; others are references only.
    for g in store.policies.get("HAZ-001").response.guidance:
        assert g in r.response
    for other in ("CYC-001", "CYC-002"):
        for g in store.policies.get(other).response.guidance:
            assert g not in r.response
    assert "not combined" in r.response


def test_priority_breaks_severity_tie(store):
    """CYC-002 (high, priority 66) beats CYC-001 (high, priority 65)."""
    metrics = {"precip_prob_max": 70, "wind_gust_max": 45, "apparent_temp_max": 25, "apparent_temp_min": 20,
               "thunderstorm": False, "uv_index_max": 3, "wind_speed_max": 20, "precip_sum": 3}
    out = match_policies(store.policies, "cycling", [], metrics, AlertContext(provider_configured=False))
    assert {m.sop_id for m in out.matches} == {"CYC-001", "CYC-002"}
    assert resolve(store.policies, out.matches).selected.sop_id == "CYC-002"


def test_group_policy_beats_general_for_children(api, store):
    """Children + heat + UV in the park: CHD-001 (high) over HAZ-006 (moderate) and PIC-* ."""
    api.forecast = make_forecast(overrides={"ALL": {"apparent_temperature": 37.0, "uv_index": 9.0}})
    r = make_service(api, store).chat("t3", "Can I take my kids to the park in Bhopal this afternoon?")
    assert r.selected_sop == "CHD-001"
    assert "HAZ-006" in r.sop_ids


def test_verified_severe_alert_overrides_activity_policies(api, store):
    """A verified alert (injected provider) makes SEV-001 win over activity-specific SOPs."""
    from app.weather.alerts import Alert

    class OneAlert:
        def get_alerts(self, loc):
            return AlertContext(provider_configured=True, alerts=[Alert(
                location="Bhopal", event="Deep depression", level="warning", source="test-feed",
                issued_at="2026-10-03T00:00:00+00:00", expires_at="2099-01-01T00:00:00+00:00")])

    api.forecast = make_forecast(overrides=RAINY_ALL_DAY)
    r = make_service(api, store, alerts=OneAlert()).chat("t4", "Is it safe to cycle in Bhopal today?")
    assert r.selected_sop == "SEV-001"
    assert "Deep depression" in r.response and "CYC-001" in r.sop_ids


def test_severe_alert_never_assumed_without_feed(api, store):
    """Even extreme rain does not trigger SEV-001 when no verified alert feed is configured."""
    api.forecast = make_forecast(overrides={"ALL": {"precipitation_probability": 100, "precipitation": 30.0}})
    r = make_service(api, store).chat("t5", "Can I drive in Bhopal today?")
    assert "SEV-001" not in r.sop_ids
    assert "No verified official alert feed" in r.response


# Test 3: paraphrase robustness
@pytest.mark.parametrize("question", [
    "Can I take my bicycle to the office in Bhopal in this weather?",
    "Is cycling safe for my commute in Bhopal today?",
    "Should I ride my two-wheeler to work in Bhopal today?",
    "Is my bicyle commute in Bhopal ok today?",  # typo -> fuzzy match
])
def test_paraphrases_map_to_same_policy(api, store, question):
    api.forecast = make_forecast(overrides=RAINY_ALL_DAY)
    r = make_service(api, store).chat("p", question)
    assert r.activity == "cycling"
    assert r.selected_sop == "CYC-001"


class FakeLLM:
    """Stands in for the constrained LLM interpreter (no API key needed)."""

    def __init__(self, payload: dict):
        self.payload = payload

    def complete_json(self, system, user):
        return self.payload

    def complete_text(self, system, user):
        raise RuntimeError("not used")


def test_llm_maps_keywordless_paraphrase(api, store):
    """No activity keyword at all; the rule interpreter finds nothing, the (fake) LLM maps it,
    and the deterministic engine still decides the SOP."""
    q = "Can I take my Splendor to the office in Bhopal today?"
    api.forecast = make_forecast(overrides=RAINY_ALL_DAY)
    assert make_service(api, store).chat("a", q).outcome == "out_of_scope"
    r = make_service(api, store, llm=FakeLLM({"activity": "cycling", "groups": [], "location": "Bhopal",
                                              "time_window": "today"})).chat("b", q)
    assert r.selected_sop == "CYC-001"


def test_llm_output_outside_vocabulary_is_discarded(api, store):
    r = make_service(api, store, llm=FakeLLM({"activity": "skydiving", "groups": ["astronauts"]})).chat(
        "c", "Can I go skydiving in Bhopal today?")
    assert r.outcome == "out_of_scope" and r.sop_ids == []


# Test 9: invalid policy configuration
def test_invalid_policy_file_rejected():
    with pytest.raises(PolicyLoadError) as exc:
        load_policies(FIXTURES / "invalid_missing_fields.yaml")
    msg = str(exc.value)
    assert "severity" in msg and "pollen_count" in msg


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["policies"].append(dict(d["policies"][0])), "duplicate"),
    (lambda d: d["policies"][1]["conditions"].update(activities=["unicycling"]), "unknown activities"),
    (lambda d: d["policies"][1].update(severity="extreme"), "severity"),
    (lambda d: d["policies"][1]["conditions"].update(all=[], any=[]), "at least one"),
    (lambda d: d["policies"][1]["response"].update(guidance=[]), "guidance"),
])
def test_invalid_policy_variants(tmp_path, mutate, needle):
    data = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    mutate(data)
    bad = tmp_path / "sops.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PolicyLoadError, match=needle):
        load_policies(bad)


def test_failed_reload_keeps_previous_valid_policies(tmp_path):
    path = tmp_path / "sops.yaml"
    shutil.copy(POLICY_FILE, path)
    store = PolicyStore(path)
    before = len(store.policies.policies)
    path.write_text("policies: [ {id: broken", encoding="utf-8")
    with pytest.raises(PolicyLoadError):
        store.reload()
    assert len(store.policies.policies) == before


def test_new_sop_added_without_code_changes(tmp_path, api):
    """Append a new SOP to a copy of the YAML, reload, and it is matched; no code touched."""
    path = tmp_path / "sops.yaml"
    data = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    data["policies"].append({
        "id": "HUM-001", "name": "Oppressive humidity for exertion", "category": "outdoor_exercise",
        "description": "Very humid air limits sweat cooling.", "severity": "critical", "priority": 95,
        "conditions": {"activities": ["running"], "all": [{"metric": "humidity_max", "op": "gte", "value": 90}]},
        "response": {"verdict": "avoid", "summary": "Humidity is extreme for running.", "guidance": ["Run indoors."]},
    })
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    store = PolicyStore(path)
    api.forecast = make_forecast(overrides={"ALL": {"relative_humidity_2m": 95}})
    r = make_service(api, store).chat("n", "Is it ok to go for a run in Bhopal today?")
    assert r.selected_sop == "HUM-001"
