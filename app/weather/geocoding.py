"""Open-Meteo geocoding with explicit ambiguity handling."""
from __future__ import annotations

import logging

import httpx

from app.weather.models import Location, WeatherError

log = logging.getLogger(__name__)


class AmbiguousLocation(Exception):
    def __init__(self, query: str, options: list[Location]):
        super().__init__(query)
        self.query = query
        self.options = options


class LocationNotFound(Exception):
    pass


# A top result is accepted when it clearly dominates other same-named places.
DOMINANCE_RATIO = 5


class Geocoder:
    def __init__(self, url: str, client: httpx.Client | None = None, timeout: float = 8.0):
        self.url = url
        self.client = client or httpx.Client(timeout=timeout)

    def resolve(self, query: str) -> Location:
        name, _, qualifier = (p.strip() for p in query.partition(","))
        try:
            resp = self.client.get(self.url, params={"name": name, "count": 10, "language": "en", "format": "json"})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("geocoding failed: %s", exc)
            raise WeatherError("geocoding_unavailable", f"Geocoding request failed: {type(exc).__name__}") from exc

        results = data.get("results") or []
        candidates = [r for r in results if r.get("name", "").lower() == name.lower()] or results
        if qualifier:
            q = qualifier.lower()
            candidates = [
                r for r in candidates
                if q in (r.get("country") or "").lower() or q in (r.get("admin1") or "").lower()
                or q == (r.get("country_code") or "").lower()
            ]
        if not candidates:
            raise LocationNotFound(query)
        return to_location(pick(query, candidates))


def pick(query: str, candidates: list[dict]) -> dict:
    """Pick the clearly intended place, or raise AmbiguousLocation."""
    if len(candidates) == 1:
        return candidates[0]
    ranked = sorted(candidates, key=lambda r: r.get("population") or 0, reverse=True)
    top, second = ranked[0], ranked[1]
    top_pop, second_pop = top.get("population") or 0, second.get("population") or 0
    if top_pop > 0 and top_pop >= DOMINANCE_RATIO * max(second_pop, 1):
        return top
    # Same-named places within one country & region are usually small villages vs. the city.
    if top_pop > 0 and second_pop == 0:
        return top
    raise AmbiguousLocation(query, [to_location(r) for r in ranked[:4]])


def to_location(r: dict) -> Location:
    try:
        return Location(
            name=r["name"], country=r.get("country"), admin1=r.get("admin1"),
            latitude=r["latitude"], longitude=r["longitude"], timezone=r.get("timezone"),
        )
    except (KeyError, ValueError) as exc:
        raise WeatherError("geocoding_invalid", "Geocoding result missing coordinates") from exc
