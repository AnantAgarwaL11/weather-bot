"""Open-Meteo forecast client: fetches, validates and aggregates a time window."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.weather.models import Location, WeatherError, WeatherReport

log = logging.getLogger(__name__)

HOURLY_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation_probability",
    "precipitation", "wind_speed_10m", "wind_gusts_10m", "uv_index", "weather_code",
]
CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation",
    "wind_speed_10m", "wind_gusts_10m", "weather_code", "uv_index", "precipitation_probability",
]
THUNDERSTORM_CODES = {95, 96, 99}

# key -> (label, day offset, start hour, end hour inclusive). None start = "from now".
TIME_WINDOWS: dict[str, tuple[str, int, int | None, int]] = {
    "now": ("right now (next 2 hours)", 0, None, -1),
    "today": ("the rest of today", 0, None, 23),
    "this_morning": ("this morning", 0, 6, 11),
    "this_afternoon": ("this afternoon", 0, 12, 16),
    "this_evening": ("this evening", 0, 17, 20),
    "tonight": ("tonight", 0, 21, 23),
    "tomorrow": ("tomorrow (daytime)", 1, 6, 20),
    "tomorrow_morning": ("tomorrow morning", 1, 6, 11),
    "tomorrow_afternoon": ("tomorrow afternoon", 1, 12, 16),
    "tomorrow_evening": ("tomorrow evening", 1, 17, 20),
}


class WeatherClient:
    def __init__(self, url: str, client: httpx.Client | None = None, timeout: float = 8.0):
        self.url = url
        self.client = client or httpx.Client(timeout=timeout)

    def fetch_raw(self, loc: Location) -> dict:
        params = {
            "latitude": loc.latitude, "longitude": loc.longitude, "timezone": "auto", "forecast_days": 3,
            "hourly": ",".join(HOURLY_FIELDS), "current": ",".join(CURRENT_FIELDS),
            "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm",
        }
        try:
            resp = self.client.get(self.url, params=params)
        except httpx.TimeoutException as exc:
            raise WeatherError("weather_timeout", "Weather service timed out") from exc
        except httpx.HTTPError as exc:
            raise WeatherError("weather_unavailable", f"Weather service unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 429:
            raise WeatherError("weather_rate_limited", "Weather service rate limit reached")
        if resp.status_code >= 400:
            raise WeatherError("weather_unavailable", f"Weather service returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise WeatherError("weather_invalid", "Weather service returned invalid JSON") from exc

    def get_report(self, loc: Location, period_key: str) -> WeatherReport:
        return build_report(self.fetch_raw(loc), loc, period_key)


def build_report(data: dict, loc: Location, period_key: str) -> WeatherReport:
    if period_key not in TIME_WINDOWS:
        raise WeatherError("period_unavailable", f"Unsupported time window {period_key}")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        raise WeatherError("weather_invalid", "Hourly forecast missing from response")
    missing = [f for f in HOURLY_FIELDS if f not in hourly]
    if missing:
        raise WeatherError("weather_invalid", f"Required fields missing: {', '.join(missing)}")

    offset = int(data.get("utc_offset_seconds", 0))
    current = data.get("current") or None
    if current and current.get("time"):
        now = datetime.fromisoformat(current["time"])
    else:
        now = (datetime.now(timezone.utc) + timedelta(seconds=offset)).replace(tzinfo=None)

    start, end = window_bounds(period_key, now)
    times = [datetime.fromisoformat(t) for t in hourly["time"]]
    idx = [i for i, t in enumerate(times) if start <= t <= end]
    if not idx:
        raise WeatherError("period_unavailable", "Requested period is past or outside the forecast range")

    def values(field: str) -> list[float]:
        col = hourly[field]
        return [col[i] for i in idx if i < len(col) and col[i] is not None]

    def agg(field: str, fn) -> float | None:
        v = values(field)
        return round(fn(v), 1) if v else None

    codes = values("weather_code")
    metrics = {
        "temp_max": agg("temperature_2m", max),
        "temp_min": agg("temperature_2m", min),
        "apparent_temp_max": agg("apparent_temperature", max),
        "apparent_temp_min": agg("apparent_temperature", min),
        "wind_speed_max": agg("wind_speed_10m", max),
        "wind_gust_max": agg("wind_gusts_10m", max),
        "precip_prob_max": agg("precipitation_probability", max),
        "precip_sum": agg("precipitation", sum),
        "uv_index_max": agg("uv_index", max),
        "humidity_max": agg("relative_humidity_2m", max),
        "thunderstorm": any(int(c) in THUNDERSTORM_CODES for c in codes) if codes else None,
    }
    label = TIME_WINDOWS[period_key][0]
    return WeatherReport(
        location=loc,
        period_key=period_key,
        period_label=label,
        kind="forecast",  # window metrics are hourly forecasts; `current` holds the model's current estimate
        start=times[idx[0]].isoformat(timespec="minutes"),
        end=times[idx[-1]].isoformat(timespec="minutes"),
        timezone=data.get("timezone") or "UTC",
        hours=len(idx),
        metrics=metrics,
        current={k: current.get(k) for k in CURRENT_FIELDS} | {"time": current.get("time")} if current else None,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def window_bounds(period_key: str, now: datetime) -> tuple[datetime, datetime]:
    _, day, start_h, end_h = TIME_WINDOWS[period_key]
    day_start = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
    hour_now = now.replace(minute=0, second=0, microsecond=0)
    if period_key == "now":
        return hour_now, hour_now + timedelta(hours=2)
    start = day_start + timedelta(hours=start_h) if start_h is not None else hour_now
    end = day_start + timedelta(hours=end_h)
    if day == 0:
        start = max(start, hour_now)  # never evaluate hours that have already passed
    return start, end
