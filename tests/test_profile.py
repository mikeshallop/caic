import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import config
import db
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-profile.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


def _admin_headers(client: TestClient) -> dict:
    login = client.post("/api/auth/login", json={"pin": "1234"}, headers={"Origin": "http://testserver"})
    sid = login.json()["session_id"]
    return {"X-Session-ID": sid, "Origin": "http://testserver"}


def _guest_headers(client: TestClient) -> dict:
    sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
    return {"X-Session-ID": sid, "Origin": "http://testserver"}


def test_get_profile_returns_content(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/profile", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert "updated_at" in data
        assert len(data["content"]) > 0


def test_get_default_profile(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/profile/default", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["content"] == config.DEFAULT_PROFILE


def test_update_profile(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.put("/api/profile", json={"content": "Custom profile text."}, headers=headers)
        assert resp.status_code == 200
        assert "updated_at" in resp.json()

        get_resp = client.get("/api/profile", headers=_guest_headers(client))
        assert get_resp.json()["content"] == "Custom profile text."


def test_update_profile_too_long(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        long_content = "x" * (config.MAX_PROFILE_CHARS + 1)
        resp = client.put("/api/profile", json={"content": long_content}, headers=headers)
        assert resp.status_code == 413


def test_guest_cannot_update_profile(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.put("/api/profile", json={"content": "hack"}, headers=_guest_headers(client))
        assert resp.status_code == 403
