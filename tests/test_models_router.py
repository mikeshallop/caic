import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import db
import routers.models
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-models.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


def _guest_headers(client: TestClient) -> dict:
    sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
    return {"X-Session-ID": sid, "Origin": "http://testserver"}


class _MockAsyncResponse:
    """Mock for httpx.AsyncClient.get/post that returns a JSON response."""
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


async def _mock_get_models(*args, **kwargs):
    return _MockAsyncResponse(json_data={
        "data": [{"id": "llama3.1:latest"}, {"id": "qwen2:latest"}]
    })


async def _mock_get_empty_models(*args, **kwargs):
    return _MockAsyncResponse(json_data={"data": []})


async def _mock_connect_error(*args, **kwargs):
    raise httpx.ConnectError("Connection refused")


async def _mock_show_model(*args, **kwargs):
    return _MockAsyncResponse(json_data={
        "modelfile": "FROM llama3.1", "parameters": {}
    })


async def _mock_search_available(*args, **kwargs):
    return _MockAsyncResponse(status_code=200)


async def _mock_search_unavailable(*args, **kwargs):
    raise httpx.ConnectError("refused")


def test_list_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get_models)
    with make_client(tmp_path) as client:
        resp = client.get("/api/models", headers=_guest_headers(client))
        assert resp.status_code == 200
        models = resp.json()["models"]
        assert len(models) == 2
        assert models[0]["name"] == "llama3.1:latest"


def test_list_models_connect_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_connect_error)
    with make_client(tmp_path) as client:
        resp = client.get("/api/models", headers=_guest_headers(client))
        assert resp.status_code == 502


def test_running_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_get_models)
    with make_client(tmp_path) as client:
        resp = client.get("/api/ps", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert "data" in resp.json()


def test_running_models_connect_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_connect_error)
    with make_client(tmp_path) as client:
        resp = client.get("/api/ps", headers=_guest_headers(client))
        assert resp.status_code == 502


def test_show_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_show_model)
    with make_client(tmp_path) as client:
        resp = client.post("/api/show", json={"model": "llama3.1:latest"}, headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["modelfile"] == "FROM llama3.1"


def test_show_model_connect_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_connect_error)
    with make_client(tmp_path) as client:
        resp = client.post("/api/show", json={"model": "llama3.1:latest"}, headers=_guest_headers(client))
        assert resp.status_code == 502


def test_system_stats(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.models, "get_gpu_stats", lambda: {"gpu_percent": 15, "vram_percent": 30, "available": True})
    with make_client(tmp_path) as client:
        resp = client.get("/api/stats", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "cpu_percent" in data
        assert "memory_percent" in data
        assert data["gpu_percent"] == 15
        assert data["gpu_available"] is True


def test_search_status_available(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_search_available)
    with make_client(tmp_path) as client:
        resp = client.get("/api/search/status", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["available"] is True


def test_search_status_unavailable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_search_unavailable)
    with make_client(tmp_path) as client:
        resp = client.get("/api/search/status", headers=_guest_headers(client))
        assert resp.status_code == 200
        assert resp.json()["available"] is False
