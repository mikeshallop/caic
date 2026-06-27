import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import db
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS, is_ip_allowed


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-ip.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app)


def test_ip_helper_allows_local_defaults():
    assert is_ip_allowed("127.0.0.1")
    assert is_ip_allowed("192.168.1.10")
    assert is_ip_allowed("10.0.0.42")
    assert is_ip_allowed("172.16.1.2")
    assert is_ip_allowed("testclient")


def test_ip_helper_blocks_public_ip():
    assert not is_ip_allowed("8.8.8.8")


def test_middleware_blocks_disallowed_ip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app, "get_client_ip", lambda _req: "8.8.8.8")
    with make_client(tmp_path) as client:
        resp = client.post("/api/auth/guest")
        assert resp.status_code == 403


def test_middleware_allows_local_ip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app, "get_client_ip", lambda _req: "192.168.50.109")
    with make_client(tmp_path) as client:
        resp = client.post("/api/auth/guest", headers={"Origin": "http://testserver"})
        assert resp.status_code == 200
