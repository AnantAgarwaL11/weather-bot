"""FastAPI endpoint tests (with mocked weather)."""
from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from fakes import POLICY_FILE, make_service
from app.main import create_app
from app.policies.loader import PolicyStore


def test_endpoints(api, store):
    client = TestClient(create_app(make_service(api, store)))
    assert client.get("/health").json()["status"] == "ok"
    body = client.post("/chat", json={"session_id": "api-1", "message": "Is it safe to cycle in Bhopal today?"}).json()
    assert body["outcome"] == "answered" and body["selected_sop"] == "CYC-003"
    assert body["weather_facts"] and body["period"]["timezone"] == "Asia/Kolkata"
    assert client.get("/policies").json()["count"] == len(store.policies.policies)
    assert len(client.get("/sessions/api-1/history").json()["messages"]) == 2
    assert client.post("/chat", json={"session_id": "bad id!", "message": "x"}).status_code == 422


def test_reload_rejects_invalid_file(api, tmp_path):
    path = tmp_path / "sops.yaml"
    shutil.copy(POLICY_FILE, path)
    store = PolicyStore(path)
    client = TestClient(create_app(make_service(api, store)))
    path.write_text("version: 1\npolicies: []\n", encoding="utf-8")
    resp = client.post("/policies/reload")
    assert resp.status_code == 422 and "Invalid policy configuration" in resp.json()["detail"]
    assert client.get("/policies").json()["count"] > 0  # previous valid set still active


def test_internal_errors_are_not_leaked(api, store):
    svc = make_service(api, store)
    svc.chat = lambda *a: (_ for _ in ()).throw(RuntimeError("secret stack detail"))
    client = TestClient(create_app(svc))
    resp = client.post("/chat", json={"session_id": "e", "message": "hi"})
    assert resp.status_code == 500 and "secret" not in resp.text
