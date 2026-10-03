"""Fake Open-Meteo APIs and service factory shared by tests and evals.
All weather produced here is SIMULATED (synthetic), never a real observation."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx

from app.agent.interpreter import Interpreter
from app.agent.nodes import Deps
from app.policies.loader import PolicyStore
from app.services.chat import ChatService
from app.weather.alerts import NoAlertProvider
from app.weather.client import WeatherClient
from app.weather.geocoding import Geocoder

ROOT = Path(__file__).resolve().parent.parent
POLICY_FILE = ROOT / "policies" / "sops.yaml"
FIXTURES = Path(__file__).parent / "fixtures"
GEO_URL = "https://geo.test/v1/search"
WX_URL = "https://wx.test/v1/forecast"

CITIES = {
    "bhopal": [{"name": "Bhopal", "latitude": 23.25, "longitude": 77.4, "country": "India", "country_code": "IN",
                "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata", "population": 1798218},
               {"name": "Bhopal", "latitude": 24.4, "longitude": 78.1, "country": "India", "country_code": "IN",
                "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata"}],
    "indore": [{"name": "Indore", "latitude": 22.72, "longitude": 75.86, "country": "India", "country_code": "IN",
                "admin1": "Madhya Pradesh", "timezone": "Asia/Kolkata", "population": 1837041}],
    "springfield": [
        {"name": "Springfield", "latitude": 39.8, "longitude": -89.6, "country": "United States", "admin1": "Illinois", "population": 116250},
        {"name": "Springfield", "latitude": 37.2, "longitude": -93.3, "country": "United States", "admin1": "Missouri", "population": 169176},
    ],
}

BASE_HOUR = {"temperature_2m": 26.0, "apparent_temperature": 27.0, "relative_humidity_2m": 55,
             "precipitation_probability": 10, "precipitation": 0.0, "wind_speed_10m": 10.0,
             "wind_gusts_10m": 18.0, "uv_index": 5.0, "weather_code": 1}
def make_forecast(now: str = "2026-10-03T08:00", default: dict | None = None,
                  overrides: dict[str, dict] | None = None, drop_fields: tuple[str, ...] = ()) -> dict:
    """Build an Open-Meteo-shaped payload for 3 days of hours.
    `overrides` maps 'HH' (today) or 'D1-HH' (tomorrow) hour keys to field overrides,
    or 'ALL' for every hour."""
    overrides = overrides or {}
    day0 = now[:10]
    d0 = date.fromisoformat(day0)
    times, hours = [], []
    for d in range(3):
        for h in range(24):
            t = f"{(d0 + timedelta(days=d)).isoformat()}T{h:02d}:00"
            row = dict(BASE_HOUR) | (default or {}) | overrides.get("ALL", {})
            key = f"{h:02d}" if d == 0 else f"D{d}-{h:02d}"
            row |= overrides.get(key, {})
            times.append(t)
            hours.append(row)
    hourly = {"time": times} | {f: [r[f] for r in hours] for f in BASE_HOUR if f not in drop_fields}
    cur_idx = times.index(now[:13] + ":00")
    current = {"time": now, "interval": 900} | {f: hours[cur_idx][f] for f in BASE_HOUR}
    return {"latitude": 23.25, "longitude": 77.4, "utc_offset_seconds": 19800, "timezone": "Asia/Kolkata",
            "current": current, "hourly": hourly}


class FakeAPI:
    """Programmable Open-Meteo stand-in served via httpx.MockTransport. Records calls."""

    def __init__(self):
        self.forecast: dict | None = make_forecast()
        self.forecast_error: Exception | int | None = None
        self.geo_error: Exception | None = None
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "geo.test":
            if self.geo_error:
                raise self.geo_error
            name = request.url.params["name"].lower()
            return httpx.Response(200, json={"results": CITIES[name]} if name in CITIES else {})
        if isinstance(self.forecast_error, Exception):
            raise self.forecast_error
        if isinstance(self.forecast_error, int):
            return httpx.Response(self.forecast_error, json={"error": True})
        return httpx.Response(200, json=self.forecast)

    def weather_calls(self) -> int:
        return sum(1 for r in self.calls if r.url.host == "wx.test")


def make_service(api: FakeAPI, store: PolicyStore, llm=None, alerts=None, rephrase=False) -> ChatService:
    client = httpx.Client(transport=httpx.MockTransport(api.handler))
    deps = Deps(
        store=store,
        interpreter=Interpreter(lambda: store.policies, llm),
        geocoder=Geocoder(GEO_URL, client=client),
        weather=WeatherClient(WX_URL, client=client),
        alerts=alerts or NoAlertProvider(),
        llm=llm,
        rephrase=rephrase,
    )
    return ChatService(deps)
