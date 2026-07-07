import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import db
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-conversations.db"
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


def test_list_conversations_empty(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/conversations", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json() == []


def test_create_and_list_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)

        create = client.post("/api/conversations", json={"title": "Test Chat", "model": "llama3.1:latest"}, headers=headers)
        assert create.status_code == 200
        data = create.json()
        assert data["title"] == "Test Chat"
        assert data["model"] == "llama3.1:latest"

        list_resp = client.get("/api/conversations", headers=headers)
        assert list_resp.status_code == 200
        convs = list_resp.json()
        assert len(convs) == 1
        assert convs[0]["title"] == "Test Chat"


def test_get_conversation_returns_messages(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/conversations", json={"title": "My Chat"}, headers=headers)
        conv_id = create.json()["id"]

        resp = client.get(f"/api/conversations/{conv_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["conversation"]["id"] == conv_id
        assert data["messages"] == []


def test_get_conversation_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/conversations/nope", headers=_guest_headers(client))
        assert resp.status_code == 404


def test_update_conversation_title(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/conversations", json={"title": "Old"}, headers=headers)
        conv_id = create.json()["id"]

        update = client.put(f"/api/conversations/{conv_id}", json={"title": "New Title"}, headers=headers)
        assert update.status_code == 200

        get = client.get(f"/api/conversations/{conv_id}", headers=headers)
        assert get.json()["conversation"]["title"] == "New Title"


def test_update_conversation_model(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/conversations", json={"title": "Test"}, headers=headers)
        conv_id = create.json()["id"]

        update = client.put(f"/api/conversations/{conv_id}", json={"model": "qwen2:latest"}, headers=headers)
        assert update.status_code == 200

        get = client.get(f"/api/conversations/{conv_id}", headers=headers)
        assert get.json()["conversation"]["model"] == "qwen2:latest"


def test_delete_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/conversations", json={"title": "Delete Me"}, headers=headers)
        conv_id = create.json()["id"]

        delete = client.delete(f"/api/conversations/{conv_id}", headers=headers)
        assert delete.status_code == 200

        get = client.get(f"/api/conversations/{conv_id}", headers=_guest_headers(client))
        assert get.status_code == 404


def test_delete_all_conversations(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        client.post("/api/conversations", json={"title": "One"}, headers=headers)
        client.post("/api/conversations", json={"title": "Two"}, headers=headers)

        delete_all = client.delete("/api/conversations", headers=headers)
        assert delete_all.status_code == 200

        list_resp = client.get("/api/conversations", headers=_guest_headers(client))
        assert list_resp.json() == []


def test_guest_cannot_create_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/conversations", json={"title": "test"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_update_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        create = client.post("/api/conversations", json={"title": "Test"}, headers=headers)
        conv_id = create.json()["id"]

        guest_headers = _guest_headers(client)
        resp = client.put(f"/api/conversations/{conv_id}", json={"title": "hack"}, headers=guest_headers)
        assert resp.status_code == 403


def test_guest_cannot_delete_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/conversations/some-id", headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_delete_all(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/conversations", headers=_guest_headers(client))
        assert resp.status_code == 403
