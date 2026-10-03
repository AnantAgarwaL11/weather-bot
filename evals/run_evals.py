"""Scenario evaluation runner.

    python evals/run_evals.py            # deterministic (SIMULATED weather) scenarios + T9
    python evals/run_evals.py --live     # also run live Open-Meteo checks

Writes a markdown table (check / input / expected / actual / PASS-FAIL) to evals/results.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fakes import FIXTURES, POLICY_FILE, FakeAPI, make_forecast, make_service  # noqa: E402
from app.policies.loader import PolicyLoadError, PolicyStore, load_policies  # noqa: E402

ERRORS = {"timeout": httpx.ConnectTimeout("timed out"), "500": 500, "429": 429}


def setup_weather(api: FakeAPI, spec: dict | None) -> None:
    spec = dict(spec or {})
    if "fixture" in spec:
        api.forecast = json.loads((FIXTURES / spec["fixture"]).read_text(encoding="utf-8"))
    elif "error" in spec:
        api.forecast_error = ERRORS[str(spec["error"])]
    else:
        now = spec.pop("now", "2026-10-03T08:00")
        api.forecast = make_forecast(now=now, overrides={str(k): v for k, v in spec.items()})


def check(expect: dict, r, api: FakeAPI) -> list[str]:
    fails = []
    for key in ("outcome", "selected_sop", "activity", "time_window"):
        if key in expect and getattr(r, key) != expect[key]:
            fails.append(f"{key}={getattr(r, key)!r} (expected {expect[key]!r})")
    if expect.get("sop_ids_empty") and r.sop_ids:
        fails.append(f"sop_ids={r.sop_ids} (expected none)")
    missing_ids = set(expect.get("sop_ids_include", [])) - set(r.sop_ids)
    if missing_ids:
        fails.append(f"missing SOP ids {sorted(missing_ids)}")
    fails += [f"missing text {t!r}" for t in expect.get("contains", []) if t not in r.response]
    fails += [f"unexpected text {t!r}" for t in expect.get("not_contains", []) if t in r.response]
    if "weather_calls" in expect and api.weather_calls() != expect["weather_calls"]:
        fails.append(f"weather_calls={api.weather_calls()} (expected {expect['weather_calls']})")
    return fails


def run_scenarios(store: PolicyStore) -> list[dict]:
    rows = []
    for sc in yaml.safe_load((Path(__file__).parent / "scenarios.yaml").read_text(encoding="utf-8")):
        api = FakeAPI()
        setup_weather(api, sc.get("weather"))
        svc = make_service(api, store)
        r = None
        for msg in sc["turns"]:
            r = svc.chat(f"eval-{sc['id']}", msg)
        fails = check(sc["expect"], r, api)
        rows.append({"id": sc["id"], "name": sc["name"], "check": sc["check"], "input": " → ".join(sc["turns"]),
                     "expected": ", ".join(f"{k}={v}" for k, v in sc["expect"].items()),
                     "actual": f"outcome={r.outcome}, selected={r.selected_sop}, sops={r.sop_ids}",
                     "status": "FAIL" if fails else "PASS", "notes": "; ".join(fails)})
    return rows


def run_invalid_policy() -> dict:
    try:
        load_policies(FIXTURES / "invalid_missing_fields.yaml")
        actual, ok = "loaded without error", False
    except PolicyLoadError as exc:
        actual, ok = f"PolicyLoadError: {str(exc)[:90]}…", True
    return {"id": "T9", "name": "Invalid policy configuration", "check": "Malformed SOP file is rejected loudly",
            "input": "tests/fixtures/invalid_missing_fields.yaml", "expected": "PolicyLoadError",
            "actual": actual, "status": "PASS" if ok else "FAIL", "notes": ""}


def run_live() -> list[dict]:
    from app.services.chat import ChatService
    svc = ChatService.from_settings()
    svc.deps.interpreter.llm, svc.deps.rephrase = None, False
    rows = []
    for sid, q in [("L1", "Is it safe to cycle in Bhopal today?"), ("L2", "Can I go for a walk in Yakutsk today?")]:
        r = svc.chat(f"live-{sid}", q)
        ok = r.outcome != "weather_unavailable" and bool(r.weather_facts)
        facts = ", ".join(f.render() for f in r.weather_facts[:4])
        rows.append({"id": sid, "name": "LIVE Open-Meteo", "check": "Real geocoding + forecast retrieved and evaluated",
                     "input": q, "expected": "weather retrieved; SOP decided from actual values",
                     "actual": f"outcome={r.outcome}, selected={r.selected_sop} ({r.severity}); {facts}",
                     "status": "PASS" if ok else "FAIL", "notes": "live values change over time"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    store = PolicyStore(POLICY_FILE)
    rows = run_scenarios(store) + [run_invalid_policy()] + (run_live() if args.live else [])
    lines = ["# Evaluation results", "", "Deterministic rows use SIMULATED weather fixtures; LIVE rows hit Open-Meteo.", "",
             "| ID | Scenario | What is checked | Input | Expected | Actual | Status |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        actual = r["actual"] + (f" — {r['notes']}" if r["notes"] and r["status"] == "FAIL" else "")
        lines.append(f"| {r['id']} | {r['name']} | {r['check']} | {r['input']} | {r['expected']} | {actual} | **{r['status']}** |")
    passed = sum(r["status"] == "PASS" for r in rows)
    lines += ["", f"**{passed}/{len(rows)} passed**"]
    out = Path(__file__).parent / "results.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['status']:4}  {r['id']:4} {r['name']}" + (f"  -- {r['notes']}" if r["status"] == "FAIL" else ""))
    print(f"{passed}/{len(rows)} passed -> {out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
