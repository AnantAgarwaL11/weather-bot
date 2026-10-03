"""LangGraph state. Plain dicts/lists only so the checkpointer can serialise it.

`messages` and `context` persist across turns of a session (thread_id);
every other key is per-turn and is reset by ChatService on each invocation.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    messages: Annotated[list[dict], operator.add]
    context: dict[str, Any]  # location_query, location, activity, time_window, groups, pending, last_sop_ids
    # per-turn
    user_message: str
    intent: dict | None
    location: dict | None
    weather: dict | None
    alerts: dict | None
    matches: list[dict]
    unevaluable: list[str]
    resolution: dict | None
    outcome: str | None
    detail: dict
    response: str | None
    limitations: list[str]


PER_TURN_RESET: dict[str, Any] = {
    "intent": None, "location": None, "weather": None, "alerts": None, "matches": [],
    "unevaluable": [], "resolution": None, "outcome": None, "detail": {}, "response": None, "limitations": [],
}
