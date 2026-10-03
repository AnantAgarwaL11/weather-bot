"""FastAPI entrypoint: `uvicorn app.main:app --reload`."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.routes import router
from app.services.chat import ChatService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(service: ChatService | None = None) -> FastAPI:
    app = FastAPI(title="Weather-Advisory Support Bot", version="1.0.0")
    app.state.chat_service = service or ChatService.from_settings()  # fails fast on invalid SOP file
    app.include_router(router)
    return app


app = create_app()
