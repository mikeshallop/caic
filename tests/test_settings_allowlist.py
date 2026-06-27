import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import db
from security import SESSIONS, PIN_ATTEMPTS


def make_admin_client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-settings.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    db.init_db()

    client = TestClient(app.app)
    login = client.post(
        "/api/auth/login",
        json={"pin": "1234"},
        headers={"Origin": "http://testserver"},
    )
    assert login.status_code == 200
    sid = login.json()["session_id"]
    headers = {"X-Session-ID": sid, "Origin": "http://testserver"}
    return client, headers


def test_settings_allow_known_keys(tmp_path: Path):
    client, headers = make_admin_client(tmp_path)
    try:
        resp = client.put(
            "/api/settings",
            json={
                "profile_enabled": "false",
                "search_enabled": "true",
                "memory_enabled": "false",
                "default_model": "llama3.1:latest",
            },
            headers=headers,
        )
        assert resp.status_code == 200
    finally:
        client.close()


def test_settings_reject_unknown_keys(tmp_path: Path):
    client, headers = make_admin_client(tmp_path)
    try:
        resp = client.put(
            "/api/settings",
            json={"admin_pin_hash": "oops"},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "Unknown setting key" in resp.json().get("detail", "")
    finally:
        client.close()
