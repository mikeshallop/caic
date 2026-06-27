import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import config
import db
import security
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-rate.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app)


def test_stats_rate_limit_hits_429(tmp_path: Path):
    old_limit = security.RL_STATS_PER_WINDOW
    old_window = app.RATE_WINDOW_SECONDS
    security.RL_STATS_PER_WINDOW = 2
    app.RATE_WINDOW_SECONDS = 60
    try:
        with make_client(tmp_path) as client:
            sid = client.post("/api/auth/guest").json()["session_id"]
            headers = {"X-Session-ID": sid}

            r1 = client.get("/api/stats", headers=headers)
            r2 = client.get("/api/stats", headers=headers)
            r3 = client.get("/api/stats", headers=headers)

            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r3.status_code == 429
    finally:
        security.RL_STATS_PER_WINDOW = old_limit
        app.RATE_WINDOW_SECONDS = old_window


def test_large_login_payload_rejected_413(tmp_path: Path):
    with make_client(tmp_path) as client:
        huge_pin = "1" * (config.BODY_LIMIT_DEFAULT_BYTES + 100)
        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"pin": huge_pin}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413


def test_chat_message_length_rejected_413(tmp_path: Path):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest").json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}
        message = "x" * (config.MAX_CHAT_MESSAGE_CHARS + 1)
        resp = client.post(
            "/api/chat",
            json={"message": message, "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 413


def test_search_query_length_rejected_413(tmp_path: Path):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest").json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}
        query = "q" * (config.MAX_SEARCH_QUERY_CHARS + 1)
        resp = client.post(
            "/api/search",
            json={"query": query, "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 413
