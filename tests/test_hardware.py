import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx
import psutil
from fastapi.testclient import TestClient

import app as app_module
import config
import db
import hardware
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-hardware.db"
    hardware.HARDWARE_STATE_PATH = tmp_path / "hardware_state.json"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app_module.app, raise_server_exceptions=False)


def _guest_headers(client: TestClient) -> dict:
    sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
    return {"X-Session-ID": sid, "Origin": "http://testserver"}


class _MockGet:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def _mock_subprocess(rocm_stdout: str = "") -> object:
    class MockProc:
        returncode = 0
        stdout = rocm_stdout

    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: MockProc())

    return MockSP()


def _broken_subprocess(exception: Exception) -> object:
    class MockSP:
        TimeoutExpired = subprocess.TimeoutExpired
        run = staticmethod(lambda cmd, **kw: (_ for _ in ()).throw(exception))

    return MockSP()


def test_assess_hardware_all_services_reachable(tmp_path: Path, monkeypatch):
    hardware.HARDWARE_STATE_PATH = tmp_path / "hardware_state.json"
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("M", (), {"total": 16 * 1024 ** 3, "available": 8 * 1024 ** 3})())
    monkeypatch.setattr(psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "subprocess", _mock_subprocess(
        json.dumps({"card0": {"VRAM Total (MB)": 8192, "VRAM Free (MB)": 4096}})
    ))

    async def mock_get(self, url, *args, **kwargs):
        if "v1/models" in url:
            return _MockGet(200, {"data": [{"id": "mistral-nemo:latest"}]})
        if "6333" in url:
            return _MockGet(200, {"result": {"collections": [{"name": "caic"}]}})
        if "8888" in url:
            return _MockGet(200, {})
        return _MockGet(200, {})
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    state = asyncio.run(hardware.assess_hardware())

    assert state["ram_total_gb"] == 16.0
    assert state["ram_available_gb"] == 8.0
    assert state["cpu_count"] == 8
    assert state["vram_total_mb"] == 8192
    assert state["vram_free_mb"] == 4096
    assert state["llama_reachable"] is True
    assert state["llama_models"] == ["mistral-nemo:latest"]
    assert state["qdrant_reachable"] is True
    assert state["qdrant_collections"] == ["caic"]
    assert state["searxng_reachable"] is True
    assert tmp_path.joinpath("hardware_state.json").exists()


def test_assess_hardware_rocm_smi_absent(tmp_path: Path, monkeypatch):
    hardware.HARDWARE_STATE_PATH = tmp_path / "hardware_state.json"
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("M", (), {"total": 16 * 1024 ** 3, "available": 8 * 1024 ** 3})())
    monkeypatch.setattr(psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "subprocess", _broken_subprocess(FileNotFoundError("no rocm-smi")))

    async def mock_get(self, url, *args, **kwargs):
        if "v1/models" in url:
            return _MockGet(200, {"data": []})
        if "6333" in url:
            return _MockGet(200, {"result": {"collections": []}})
        if "8888" in url:
            return _MockGet(200, {})
        return _MockGet(200, {})
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    state = asyncio.run(hardware.assess_hardware())

    assert state["vram_total_mb"] == 0
    assert state["vram_free_mb"] == 0
    assert state["llama_reachable"] is True
    assert state["qdrant_reachable"] is True
    assert state["searxng_reachable"] is True


def test_assess_hardware_llama_unreachable(tmp_path: Path, monkeypatch):
    hardware.HARDWARE_STATE_PATH = tmp_path / "hardware_state.json"
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("M", (), {"total": 16 * 1024 ** 3, "available": 8 * 1024 ** 3})())
    monkeypatch.setattr(psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "subprocess", _mock_subprocess(
        json.dumps({"card0": {"VRAM Total (MB)": 8192, "VRAM Free (MB)": 4096}})
    ))

    async def mock_get(self, url, *args, **kwargs):
        if "v1/models" in url:
            raise httpx.ConnectError("refused")
        if "6333" in url:
            return _MockGet(200, {"result": {"collections": [{"name": "caic"}]}})
        if "8888" in url:
            return _MockGet(200, {})
        return _MockGet(200, {})
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    state = asyncio.run(hardware.assess_hardware())

    assert state["llama_reachable"] is False
    assert state["llama_models"] == []
    assert state["qdrant_reachable"] is True
    assert state["searxng_reachable"] is True


def test_get_hardware_endpoint(tmp_path: Path, monkeypatch):
    hardware.HARDWARE_STATE_PATH = tmp_path / "hardware_state.json"
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("M", (), {"total": 16 * 1024 ** 3, "available": 8 * 1024 ** 3})())
    monkeypatch.setattr(psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "subprocess", _mock_subprocess(
        json.dumps({"card0": {"VRAM Total (MB)": 8192, "VRAM Free (MB)": 4096}})
    ))

    async def mock_get(self, url, *args, **kwargs):
        if "v1/models" in url:
            return _MockGet(200, {"data": [{"id": "mistral-nemo:latest"}]})
        if "6333" in url:
            return _MockGet(200, {"result": {"collections": [{"name": "caic"}]}})
        if "8888" in url:
            return _MockGet(200, {})
        return _MockGet(200, {})
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    with make_client(tmp_path) as client:
        resp = client.get("/api/hardware", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["ram_total_gb"] == 16.0
        assert data["cpu_count"] == 8
        assert data["vram_total_mb"] == 8192
        assert data["llama_reachable"] is True
        assert data["qdrant_reachable"] is True
        assert data["searxng_reachable"] is True
        assert "llama_models" in data
        assert "qdrant_collections" in data
