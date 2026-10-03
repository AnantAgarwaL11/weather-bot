"""HTTP routes. Thin: validation + delegation to ChatService / PolicyStore."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.policies.loader import PolicyLoadError
from app.services.chat import ChatResult, ChatService

log = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    message: str = Field(min_length=1, max_length=1000)


def _service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@router.get("/health")
def health(request: Request) -> dict:
    svc = _service(request)
    return {"status": "ok", "policies_loaded": len(svc.deps.store.policies.policies),
            "llm_enabled": svc.deps.llm is not None}


@router.post("/chat", response_model=ChatResult)
def chat(req: ChatRequest, request: Request) -> ChatResult:
    try:
        return _service(request).chat(req.session_id, req.message.strip())
    except Exception:
        log.exception("chat failed")  # full trace stays in server logs only
        raise HTTPException(status_code=500, detail="Internal error; no recommendation was made.")


@router.get("/sessions/{session_id}/history")
def history(session_id: str, request: Request) -> dict:
    return {"session_id": session_id, "messages": _service(request).history(session_id)}


@router.get("/policies")
def policies(request: Request) -> dict:
    ps = _service(request).deps.store.policies
    return {"version": ps.version, "count": len(ps.policies),
            "activities": {k: v.label for k, v in ps.activities.items()},
            "policies": [p.model_dump() for p in ps.policies]}


@router.post("/policies/reload")
def reload_policies(request: Request) -> dict:
    try:
        ps = _service(request).deps.store.reload()
    except PolicyLoadError as exc:
        # The previously loaded valid policy set stays active.
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "reloaded", "count": len(ps.policies)}
