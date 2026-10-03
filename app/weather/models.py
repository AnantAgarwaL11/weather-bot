"""Structured, validated weather and location models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WeatherError(Exception):
    """Weather could not be retrieved or failed validation. `kind` is user-safe."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class Location(BaseModel):
    name: str
    country: str | None = None
    admin1: str | None = None
    latitude: float
    longitude: float
    timezone: str | None = None

    @property
    def display(self) -> str:
        return ", ".join(p for p in (self.name, self.admin1, self.country) if p)


# Metric name -> (human label, unit). Single source of truth for units.
METRICS: dict[str, tuple[str, str]] = {
    "temp_max": ("Max temperature", "°C"),
    "temp_min": ("Min temperature", "°C"),
    "apparent_temp_max": ("Max feels-like temperature", "°C"),
    "apparent_temp_min": ("Min feels-like temperature", "°C"),
    "wind_speed_max": ("Max wind speed", "km/h"),
    "wind_gust_max": ("Max wind gusts", "km/h"),
    "precip_prob_max": ("Max precipitation probability", "%"),
    "precip_sum": ("Total precipitation", "mm"),
    "uv_index_max": ("Max UV index", ""),
    "humidity_max": ("Max relative humidity", "%"),
    "thunderstorm": ("Thunderstorm forecast", ""),
}


class WeatherFact(BaseModel):
    metric: str
    label: str
    value: float | bool
    unit: str

    def render(self) -> str:
        if isinstance(self.value, bool):
            return f"{self.label}: {'yes' if self.value else 'no'}"
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.label}: {format_number(self.value)}{unit}"


class WeatherReport(BaseModel):
    """Weather aggregated over the requested window. Values are never fabricated:
    a metric is None if the provider returned no data for it."""

    location: Location
    period_key: str
    period_label: str
    kind: Literal["forecast"] = "forecast"
    start: str  # local ISO time
    end: str
    timezone: str
    hours: int
    metrics: dict[str, float | bool | None]
    current: dict[str, float | str | None] | None = None  # model "current conditions" (+ "time")
    source: str = "Open-Meteo"
    retrieved_at: str
    units: dict[str, str] = Field(default_factory=lambda: {k: u for k, (_, u) in METRICS.items()})

    def fact(self, metric: str) -> WeatherFact | None:
        value = self.metrics.get(metric)
        if value is None:
            return None
        label, unit = METRICS[metric]
        return WeatherFact(metric=metric, label=label, value=value, unit=unit)

    def facts(self) -> list[WeatherFact]:
        return [f for m in METRICS if (f := self.fact(m)) is not None]


def format_number(value: float) -> str:
    return f"{value:g}" if float(value) != int(value) else str(int(value))
