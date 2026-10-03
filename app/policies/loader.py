"""Load and strictly validate the external SOP file."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.weather.models import METRICS

log = logging.getLogger(__name__)

SEVERITY_RANK = {"info": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
Op = Literal["gt", "gte", "lt", "lte", "eq"]


class PolicyLoadError(Exception):
    pass


class Condition(BaseModel, extra="forbid"):
    metric: str
    op: Op
    value: float | bool

    @field_validator("metric")
    @classmethod
    def known_metric(cls, v: str) -> str:
        if v not in METRICS:
            raise ValueError(f"unknown metric '{v}'")
        return v

    @model_validator(mode="after")
    def type_matches(self) -> "Condition":
        if self.metric == "thunderstorm" and (self.op != "eq" or not isinstance(self.value, bool)):
            raise ValueError("thunderstorm condition must be 'eq' with a boolean value")
        if self.metric != "thunderstorm" and isinstance(self.value, bool):
            raise ValueError(f"metric '{self.metric}' needs a numeric value")
        return self


class AlertCondition(BaseModel, extra="forbid"):
    min_level: Literal["advisory", "watch", "warning", "emergency"]


class Conditions(BaseModel, extra="forbid"):
    activities: list[str] | Literal["any"]
    groups: list[str] = []
    all: list[Condition] = []
    any: list[Condition] = []
    alert: AlertCondition | None = None

    @model_validator(mode="after")
    def has_evidence(self) -> "Conditions":
        if not (self.all or self.any or self.alert):
            raise ValueError("conditions need at least one of all/any/alert")
        return self


class PolicyResponse(BaseModel, extra="forbid"):
    verdict: Literal["ok", "caution", "not_recommended", "avoid"]
    summary: str = Field(min_length=5)
    guidance: list[str] = Field(min_length=1)


class SOP(BaseModel, extra="forbid"):
    id: str = Field(pattern=r"^[A-Z]{2,5}-\d{3}$")
    name: str = Field(min_length=3)
    category: str
    description: str = Field(min_length=5)
    severity: Literal["info", "low", "moderate", "high", "critical"]
    priority: int = 0
    conditions: Conditions
    response: PolicyResponse
    metadata: dict = {}

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK[self.severity]

    @property
    def metrics(self) -> list[str]:
        return [c.metric for c in self.conditions.all + self.conditions.any]


class Vocab(BaseModel, extra="forbid"):
    label: str
    synonyms: list[str] = Field(min_length=1)


class PolicySet(BaseModel, extra="forbid"):
    version: str
    activities: dict[str, Vocab]
    groups: dict[str, Vocab]
    policies: list[SOP] = Field(min_length=1)

    @model_validator(mode="after")
    def cross_check(self) -> "PolicySet":
        ids = [p.id for p in self.policies]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate SOP ids: {sorted(dupes)}")
        for p in self.policies:
            acts = p.conditions.activities
            if acts != "any":
                unknown = set(acts) - set(self.activities)
                if unknown:
                    raise ValueError(f"{p.id}: unknown activities {sorted(unknown)}")
            unknown_groups = set(p.conditions.groups) - set(self.groups)
            if unknown_groups:
                raise ValueError(f"{p.id}: unknown groups {sorted(unknown_groups)}")
        return self

    def get(self, sop_id: str) -> SOP | None:
        return next((p for p in self.policies if p.id == sop_id), None)


def load_policies(path: Path) -> PolicySet:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyLoadError(f"Policy file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"Policy file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyLoadError("Policy file must be a mapping with version/activities/groups/policies")
    try:
        return PolicySet.model_validate(raw)
    except ValidationError as exc:
        errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:10])
        raise PolicyLoadError(f"Invalid policy configuration: {errors}") from exc


class PolicyStore:
    """Holds the active PolicySet. Reloads are all-or-nothing: an invalid file never
    replaces a valid set, and the error is surfaced to the caller."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._policies = load_policies(self.path)

    @property
    def policies(self) -> PolicySet:
        return self._policies

    def reload(self) -> PolicySet:
        new = load_policies(self.path)
        with self._lock:
            self._policies = new
        log.info("reloaded %d SOPs from %s", len(new.policies), self.path)
        return new
