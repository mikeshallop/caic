import os
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    app_module.DB_PATH = tmp_path / "jarvischat-ip.db"
    app_module.SESSIONS.clear()
    app_module.PIN_ATTEMPTS.clear()
    app_module.RATE_EVENTS.clear()
    app_module.init_db()
    return TestClient(app_module.app)


def test_ip_helper_allows_local_defaults():
    assert app_module.is_ip_allowed("127.0.0.1")
    assert app_module.is_ip_allowed("192.168.1.10")
    assert app_module.is_ip_allowed("10.0.0.42")
    assert app_module.is_ip_allowed("172.16.1.2")
    assert app_module.is_ip_allowed("testclient")


def test_ip_helper_blocks_public_ip():
    assert not app_module.is_ip_allowed("8.8.8.8")


def test_middleware_blocks_disallowed_ip(tmp_path: Path):
    with make_client(tmp_path) as client:
        original_get_client_ip = app_module.get_client_ip
        try:
            app_module.get_client_ip = lambda _req: "8.8.8.8"
            resp = client.post("/api/auth/guest")
            assert resp.status_code == 403
        finally:
            app_module.get_client_ip = original_get_client_ip


def test_middleware_allows_local_ip(tmp_path: Path):
    with make_client(tmp_path) as client:
        original_get_client_ip = app_module.get_client_ip
        try:
            app_module.get_client_ip = lambda _req: "192.168.50.109"
            resp = client.post("/api/auth/guest")
            assert resp.status_code == 200
        finally:
            app_module.get_client_ip = original_get_client_ip
