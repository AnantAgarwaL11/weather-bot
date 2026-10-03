"""Chat service: builds dependencies, runs the graph per session, shapes results."""
from __future__ import annotations

import logging

from pydantic import BaseModel

from app.agent.graph import build_graph
from app.agent.interpreter import Interpreter
from app.agent.llm import OpenAIClient
from app.agent.nodes import Deps
from app.agent.state import PER_TURN_RESET
from app.config import Settings, get_settings
from app.policies.loader import PolicyStore
from app.weather.alerts import FileAlertProvider, NoAlertProvider
from app.weather.client import WeatherClient
from app.weather.geocoding import Geocoder
from app.weather.models import WeatherFact, WeatherReport

log = logging.getLogger(__name__)


class ChatResult(BaseModel):
    session_id: str
    response: str
    outcome: str
    sop_ids: list[str] = []
    selected_sop: str | None = None
    severity: str | None = None
    resolution_rationale: str | None = None
    location: str | None = None
    time_window: str | None = None
    period: dict | None = None
    weather_facts: list[WeatherFact] = []
    current_conditions: dict | None = None
    activity: str | None = None
    limitations: list[str] = []


def build_deps(settings: Settings, store: PolicyStore | None = None) -> Deps:
    store = store or PolicyStore(settings.policy_file)
    llm = OpenAIClient(settings.openai_api_key, settings.openai_model) if settings.llm_enabled else None
    return Deps(
        store=store,
        interpreter=Interpreter(lambda: store.policies, llm),
        geocoder=Geocoder(settings.geocoding_url, timeout=settings.http_timeout),
        weather=WeatherClient(settings.forecast_url, timeout=settings.http_timeout),
        alerts=FileAlertProvider(settings.alerts_file) if settings.alerts_file else NoAlertProvider(),
        llm=llm,
        rephrase=settings.llm_rephrase,
    )


class ChatService:
    def __init__(self, deps: Deps, checkpointer=None):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ChatService":
        return cls(build_deps(settings or get_settings()))

    def chat(self, session_id: str, message: str) -> ChatResult:
        config = {"configurable": {"thread_id": session_id}}
        state_in = {**PER_TURN_RESET, "user_message": message, "messages": [{"role": "user", "content": message}]}
        state = self.graph.invoke(state_in, config)
        return self._result(session_id, state)

    def history(self, session_id: str) -> list[dict]:
        snap = self.graph.get_state({"configurable": {"thread_id": session_id}})
        return list(snap.values.get("messages", [])) if snap and snap.values else []

    def _result(self, session_id: str, state: dict) -> ChatResult:
        ctx = state.get("context") or {}
        result = ChatResult(session_id=session_id, response=state.get("response") or "",
                            outcome=state.get("outcome") or "error", activity=ctx.get("activity"),
                            time_window=ctx.get("time_window"))
        if state.get("weather"):
            report = WeatherReport(**state["weather"])
            result.location = report.location.display
            result.period = {"key": report.period_key, "label": report.period_label, "start": report.start,
                             "end": report.end, "timezone": report.timezone, "kind": report.kind}
            result.weather_facts = report.facts()
            result.current_conditions = report.current
        elif state.get("location"):
            result.location = state["location"].get("name")
        if state.get("resolution") and result.outcome == "answered":
            res = state["resolution"]
            sop = self.deps.store.policies.get(res["selected"]["sop_id"])
            result.selected_sop = sop.id
            result.severity = sop.severity
            result.sop_ids = [sop.id] + res["others"]
            result.resolution_rationale = res["rationale"]
            result.limitations = state.get("limitations") or []
        return result
