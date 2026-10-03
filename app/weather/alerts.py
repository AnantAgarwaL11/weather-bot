"""Authoritative severe-weather alert interface.

Open-Meteo forecasts do NOT establish that an official warning (cyclone, depression,
low-pressure system) is active, so SEV-* policies only fire on alerts from an
explicitly configured, verified provider. The MVP ships with no live feed:
`NoAlertProvider` is the default and `FileAlertProvider` lets an operator/test
supply alerts (e.g. exported from a national met-service CAP feed).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from app.weather.models import Location

ALERT_LEVELS = ["advisory", "watch", "warning", "emergency"]


class Alert(BaseModel):
    location: str  # place name the alert covers (case-insensitive match)
    event: str
    level: Literal["advisory", "watch", "warning", "emergency"]
    source: str
    issued_at: str
    expires_at: str


class AlertContext(BaseModel):
    provider_configured: bool
    alerts: list[Alert] = []


class AlertProvider(Protocol):
    def get_alerts(self, loc: Location) -> AlertContext: ...


class NoAlertProvider:
    def get_alerts(self, loc: Location) -> AlertContext:
        return AlertContext(provider_configured=False)


class FileAlertProvider:
    def __init__(self, path: Path):
        self.path = path

    def get_alerts(self, loc: Location) -> AlertContext:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc)
        active = [
            a for a in (Alert(**item) for item in raw.get("alerts", []))
            if a.location.lower() == loc.name.lower() and datetime.fromisoformat(a.expires_at) > now
        ]
        return AlertContext(provider_configured=True, alerts=active)
