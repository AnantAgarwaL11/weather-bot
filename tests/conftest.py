"""Shared pytest fixtures. Weather/geocoding go through the REAL client code via
httpx.MockTransport (see fakes.py); all fixture weather is SIMULATED."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fakes import POLICY_FILE, FakeAPI, make_service  # noqa: E402
from app.policies.loader import PolicyStore  # noqa: E402
from app.services.chat import ChatService  # noqa: E402


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def store() -> PolicyStore:
    return PolicyStore(POLICY_FILE)


@pytest.fixture
def service(api, store) -> ChatService:
    return make_service(api, store)
