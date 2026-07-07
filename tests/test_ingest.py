import json
import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import db
import routers.ingest as ingest_route
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-ingest.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


TEST_API_KEY = "test-sk-caic-ingest"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {TEST_API_KEY}", "Content-Type": "application/json", "Origin": "http://testserver"}


def test_ingest_missing_api_key(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/ingest", json={"content": "test"}, headers={"Origin": "http://testserver"})
        assert resp.status_code == 401


def test_ingest_wrong_api_key(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/ingest", json={"content": "test"},
                           headers={"Authorization": "Bearer wrong", "Origin": "http://testserver"})
        assert resp.status_code == 401


def test_ingest_empty_content(tmp_path: Path):
    monkeypatch = __import__('pytest').MonkeyPatch()
    monkeypatch.setattr(ingest_route, "COMPLETIONS_API_KEY", TEST_API_KEY)
    with make_client(tmp_path) as client:
        resp = client.post("/api/ingest", json={"content": ""}, headers=_auth_headers())
        assert resp.status_code == 422
    monkeypatch.undo()


def test_ingest_missing_content(tmp_path: Path):
    monkeypatch = __import__('pytest').MonkeyPatch()
    monkeypatch.setattr(ingest_route, "COMPLETIONS_API_KEY", TEST_API_KEY)
    with make_client(tmp_path) as client:
        resp = client.post("/api/ingest", json={}, headers=_auth_headers())
        assert resp.status_code == 422
    monkeypatch.undo()


def test_ingest_success(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_route, "COMPLETIONS_API_KEY", TEST_API_KEY)

    embed_count = 0

    class FakeAsyncClient:
        class FakeResponse:
            def __init__(self, status, json_data=None):
                self.status_code = status
                self._json = json_data or {}

            def json(self):
                return self._json

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            nonlocal embed_count
            if "/api/embeddings" in url:
                embed_count += 1
                return self.FakeResponse(200, {"embedding": [0.1] * 768})
            return self.FakeResponse(200)

        async def put(self, url, **kw):
            return self.FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())

    with make_client(tmp_path) as client:
        resp = client.post("/api/ingest", json={"content": "test " * 1000, "source": "terminal"}, headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "terminal"
        assert data["chunks_ingested"] > 0
        assert embed_count == data["chunks_ingested"]
        assert "message" in data
