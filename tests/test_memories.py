import os
from pathlib import Path

from fastapi.testclient import TestClient

import app
import config
import db
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-memories.db"
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


def _create_memory(client: TestClient, headers: dict, fact: str = "test fact", topic: str = "general") -> int:
    resp = client.post("/api/memories", json={"fact": fact, "topic": topic}, headers=headers)
    assert resp.status_code == 200
    return resp.json()["rowid"]


def test_list_memories_empty(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/memories", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


def test_list_memories_by_topic(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        _create_memory(client, headers, "I like Python", "preference")
        _create_memory(client, headers, "Building a game", "project")

        general = client.get("/api/memories?topic=preference", headers=_guest_headers(client))
        assert general.json()["count"] == 1
        assert general.json()["memories"][0]["topic"] == "preference"


def test_create_memory_requires_fact(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/memories", json={"fact": ""}, headers=_admin_headers(client))
        assert resp.status_code == 400


def test_create_memory_too_long(tmp_path: Path):
    with make_client(tmp_path) as client:
        long_fact = "x" * (config.MAX_MEMORY_FACT_CHARS + 1)
        resp = client.post("/api/memories", json={"fact": long_fact}, headers=_admin_headers(client))
        assert resp.status_code == 413


def test_edit_memory(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        rowid = _create_memory(client, headers, "original fact")

        edit = client.put(f"/api/memories/{rowid}", json={"fact": "updated fact"}, headers=headers)
        assert edit.status_code == 200

        memories = client.get("/api/memories", headers=_guest_headers(client)).json()
        assert any(m["fact"] == "updated fact" for m in memories["memories"])


def test_edit_memory_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.put("/api/memories/99999", json={"fact": "nope"}, headers=_admin_headers(client))
        assert resp.status_code == 404


def test_edit_memory_empty_fact(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        rowid = _create_memory(client, headers, "some fact")
        resp = client.put(f"/api/memories/{rowid}", json={"fact": ""}, headers=headers)
        assert resp.status_code == 400


def test_edit_memory_too_long(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        rowid = _create_memory(client, headers, "some fact")
        long_fact = "x" * (config.MAX_MEMORY_FACT_CHARS + 1)
        resp = client.put(f"/api/memories/{rowid}", json={"fact": long_fact}, headers=headers)
        assert resp.status_code == 413


def test_delete_memory_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/memories/99999", headers=_admin_headers(client))
        assert resp.status_code == 404


def test_search_memories(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        _create_memory(client, headers, "my favorite color is blue", "preference")
        _create_memory(client, headers, "running nginx on port 443", "infrastructure")

        resp = client.get("/api/memories/search?q=nginx&limit=5", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any("nginx" in r["fact"] for r in data["results"])


def test_search_memories_no_results(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.get("/api/memories/search?q=xyznonexistent&limit=5", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


def test_memory_stats(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        _create_memory(client, headers, "like rust", "preference")
        _create_memory(client, headers, "like python", "preference")
        _create_memory(client, headers, "project game", "project")

        resp = client.get("/api/memories/stats", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["by_topic"]["preference"] == 2
        assert data["by_topic"]["project"] == 1


def test_guest_cannot_create_memory(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/memories", json={"fact": "hack"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_edit_memory(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.put("/api/memories/1", json={"fact": "hack"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_guest_cannot_delete_memory(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/memories/1", headers=_guest_headers(client))
        assert resp.status_code == 403
