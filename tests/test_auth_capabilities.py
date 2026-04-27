import os
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    app_module.DB_PATH = tmp_path / "jarvischat-test.db"
    app_module.SESSIONS.clear()
    app_module.PIN_ATTEMPTS.clear()
    app_module.init_db()
    return TestClient(app_module.app)


def test_guest_read_only_admin_write_blocked(tmp_path: Path):
    with make_client(tmp_path) as client:
        guest = client.post("/api/auth/guest", headers={"Origin": "http://testserver"})
        assert guest.status_code == 200
        sid = guest.json()["session_id"]
        headers = {"X-Session-ID": sid}

        read_resp = client.get("/api/memories", headers=headers)
        assert read_resp.status_code == 200

        write_resp = client.post(
            "/api/memories",
            json={"fact": "guest write should fail", "topic": "general"},
            headers={**headers, "Origin": "http://testserver"},
        )
        assert write_resp.status_code == 403


def test_admin_can_write_and_delete_memory(tmp_path: Path):
    with make_client(tmp_path) as client:
        login = client.post(
            "/api/auth/login",
            json={"pin": "1234"},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200
        sid = login.json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        create_resp = client.post(
            "/api/memories",
            json={"fact": "admin write ok", "topic": "general"},
            headers=headers,
        )
        assert create_resp.status_code == 200
        rowid = create_resp.json()["rowid"]

        delete_resp = client.delete(f"/api/memories/{rowid}", headers=headers)
        assert delete_resp.status_code == 200


def test_origin_check_blocks_cross_site_writes(tmp_path: Path):
    with make_client(tmp_path) as client:
        denied = client.post("/api/auth/guest", headers={"Origin": "http://evil.example"})
        assert denied.status_code == 403

        allowed = client.post("/api/auth/guest", headers={"Origin": "http://testserver"})
        assert allowed.status_code == 200


def test_logout_revokes_session(tmp_path: Path):
    with make_client(tmp_path) as client:
        guest = client.post("/api/auth/guest", headers={"Origin": "http://testserver"})
        sid = guest.json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        logout = client.post("/api/auth/logout", headers=headers)
        assert logout.status_code == 200

        after = client.get("/api/memories", headers={"X-Session-ID": sid})
        assert after.status_code == 401
