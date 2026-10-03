"""LangGraph workflow wiring with conditional branches.

understand_query -> resolve_context -> resolve_location -> fetch_weather -> match_sops
  -> resolve_policy -> validate_decision -> generate_response
Any node that sets `outcome` diverts to handle_failure (clarification, not-found,
weather failure, no SOP, validation failure).
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import Deps, Nodes
from app.agent.state import AgentState


def _ok_or_fail(next_node: str):
    def route(state: AgentState) -> str:
        return "handle_failure" if state.get("outcome") else next_node
    route.__name__ = f"route_to_{next_node}"
    return route


def build_graph(deps: Deps, checkpointer=None):
    n = Nodes(deps)
    g = StateGraph(AgentState)
    for name in ("understand_query", "resolve_context", "resolve_location", "fetch_weather", "match_sops",
                 "resolve_policy", "validate_decision", "generate_response", "handle_failure"):
        g.add_node(name, getattr(n, name))

    g.add_edge(START, "understand_query")
    g.add_edge("understand_query", "resolve_context")
    g.add_conditional_edges("resolve_context", _ok_or_fail("resolve_location"), ["resolve_location", "handle_failure"])
    g.add_conditional_edges("resolve_location", _ok_or_fail("fetch_weather"), ["fetch_weather", "handle_failure"])
    g.add_conditional_edges("fetch_weather", _ok_or_fail("match_sops"), ["match_sops", "handle_failure"])
    g.add_conditional_edges("match_sops", _ok_or_fail("resolve_policy"), ["resolve_policy", "handle_failure"])
    g.add_edge("resolve_policy", "validate_decision")
    g.add_conditional_edges("validate_decision", _ok_or_fail("generate_response"), ["generate_response", "handle_failure"])
    g.add_edge("generate_response", END)
    g.add_edge("handle_failure", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())
