import os
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    app_module.DB_PATH = tmp_path / "jarvischat-skills.db"
    app_module.SESSIONS.clear()
    app_module.PIN_ATTEMPTS.clear()
    app_module.RATE_EVENTS.clear()
    app_module.init_db()
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_guest_can_list_skills(tmp_path: Path):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        resp = client.get("/api/skills", headers={"X-Session-ID": sid})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["count"] >= 1
        assert any(skill["key"] == "memory.search" for skill in payload["skills"])


def test_admin_can_toggle_skill_enabled_state(tmp_path: Path):
    with make_client(tmp_path) as client:
        login = client.post(
            "/api/auth/login",
            json={"pin": "1234"},
            headers={"Origin": "http://testserver"},
        )
        sid = login.json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        disable = client.put(
            "/api/skills/search.web",
            json={"enabled": False},
            headers=headers,
        )
        assert disable.status_code == 200
        assert disable.json()["skill"]["enabled"] is False

        active = client.get("/api/skills/active", headers={"X-Session-ID": sid})
        assert active.status_code == 200
        assert all(skill["key"] != "search.web" for skill in active.json()["skills"])


def test_unknown_skill_update_is_rejected(tmp_path: Path):
    with make_client(tmp_path) as client:
        login = client.post(
            "/api/auth/login",
            json={"pin": "1234"},
            headers={"Origin": "http://testserver"},
        )
        sid = login.json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        resp = client.put(
            "/api/skills/nope.unknown",
            json={"enabled": True},
            headers=headers,
        )
        assert resp.status_code == 404


def test_prompt_injection_respects_skills_enabled_setting(tmp_path: Path):
    with make_client(tmp_path):
        db = app_module.get_db()
        try:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("skills_enabled", "false"),
            )
            db.commit()
            without_skills = app_module.build_system_prompt(db, "", "hello")
            assert "## Active Skills" not in without_skills

            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("skills_enabled", "true"),
            )
            db.commit()
            with_skills = app_module.build_system_prompt(db, "", "hello")
            assert "## Active Skills" in with_skills
            assert "memory.search" in with_skills
        finally:
            db.close()
