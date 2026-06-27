import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import db
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-presets.db"
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


def test_list_presets_returns_defaults(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/presets", headers=_guest_headers(client))
        assert resp.status_code == 200
        presets = resp.json()
        assert len(presets) >= 3
        names = [p["name"] for p in presets]
        assert "Coding Companion" in names


def test_create_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.post("/api/presets", json={"name": "My Preset", "prompt": "You are helpful."}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My Preset"
        assert data["prompt"] == "You are helpful."

        presets = client.get("/api/presets", headers=_guest_headers(client)).json()
        assert any(p["name"] == "My Preset" for p in presets)


def test_create_preset_requires_name_and_prompt(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.post("/api/presets", json={"name": "", "prompt": ""}, headers=headers)
        assert resp.status_code == 400

        resp = client.post("/api/presets", json={"name": "Only Name"}, headers=headers)
        assert resp.status_code == 400


def test_update_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/presets", json={"name": "Old", "prompt": "Old prompt."}, headers=headers)
        preset_id = create.json()["id"]

        update = client.put(f"/api/presets/{preset_id}", json={"name": "New", "prompt": "New prompt."}, headers=headers)
        assert update.status_code == 200

        presets = client.get("/api/presets", headers=_guest_headers(client)).json()
        updated = next(p for p in presets if p["id"] == preset_id)
        assert updated["name"] == "New"
        assert updated["prompt"] == "New prompt."


def test_update_preset_requires_fields(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.put("/api/presets/nope", json={"name": "", "prompt": ""}, headers=headers)
        assert resp.status_code == 400


def test_delete_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/presets", json={"name": "Temp", "prompt": "Temp."}, headers=headers)
        preset_id = create.json()["id"]

        delete = client.delete(f"/api/presets/{preset_id}", headers=headers)
        assert delete.status_code == 200

        presets = client.get("/api/presets", headers=_guest_headers(client)).json()
        assert not any(p["id"] == preset_id for p in presets)


def test_delete_default_preset_is_noop(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        presets_before = client.get("/api/presets", headers=_guest_headers(client)).json()
        default = next(p for p in presets_before if p["is_default"])

        delete = client.delete(f"/api/presets/{default['id']}", headers=headers)
        assert delete.status_code == 200

        presets_after = client.get("/api/presets", headers=_guest_headers(client)).json()
        assert any(p["id"] == default["id"] for p in presets_after)


def test_guest_cannot_create_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/presets", json={"name": "Hack", "prompt": "Hack"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_update_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.put("/api/presets/some-id", json={"name": "Hack", "prompt": "Hack"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_delete_preset(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/presets/some-id", headers=_guest_headers(client))
        assert resp.status_code == 403
