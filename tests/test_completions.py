import json
import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import config
import db
import routers.completions
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-completions.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


TEST_API_KEY = "test-sk-jarvischat-completions"


def _auth_headers(extra: dict = None) -> dict:
    h = {"Authorization": f"Bearer {TEST_API_KEY}", "Content-Type": "application/json", "Origin": "http://testserver"}
    if extra:
        h.update(extra)
    return h


class _MockStreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _MockAsyncPostResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def _stream_json_lines(events: list[dict]) -> list[str]:
    return [json.dumps(event) for event in events]


def test_completions_missing_api_key(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401


def test_completions_invalid_api_key(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer wrong-key", "Origin": "http://testserver"},
        )
        assert resp.status_code == 401


def test_completions_no_messages(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    with make_client(tmp_path) as client:
        resp = client.post("/v1/chat/completions", json={}, headers=_auth_headers())
        assert resp.status_code == 400


def test_completions_empty_messages(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    with make_client(tmp_path) as client:
        resp = client.post("/v1/chat/completions", json={"messages": []}, headers=_auth_headers())
        assert resp.status_code == 400


def test_completions_no_user_message(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "assistant", "content": "hello"}]},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400


def test_completions_streaming(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    events = _stream_json_lines([
        {"choices": [{"delta": {"content": "Hello"}, "logprobs": None}]},
        {"choices": [{"delta": {"content": " world"}, "logprobs": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"tokens_per_second": 15.0}},
    ])

    call_count = 0

    def stream_stub(self, method, url, json=None, timeout=None):
        nonlocal call_count
        call_count += 1
        return _MockStreamResponse(events)

    monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.text
        assert "data: [DONE]" in body
        assert "Hello" in body or "world" in body
        assert "chatcmpl-" in body


def test_completions_blocking(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    events = _stream_json_lines([
        {"choices": [{"delta": {"content": "Hello world"}, "logprobs": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {}},
    ])

    def stream_stub(self, method, url, json=None, timeout=None):
        return _MockStreamResponse(events)

    monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "Hello world"


def test_completions_fim_passthrough(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    fim_data = {"prompt": "def foo():\n    ", "suffix": "\n    return x", "model": "llama3.1:latest"}

    async def mock_post(self, url, json=None, timeout=None):
        return _MockAsyncPostResponse(json_data={"choices": [{"text": "pass"}], "usage": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    with make_client(tmp_path) as client:
        resp = client.post("/v1/chat/completions", json=fim_data, headers=_auth_headers())
        assert resp.status_code == 200
        assert "choices" in resp.json()


def test_completions_connect_error_stream(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)

    def broken_stream(self, method, url, json=None, timeout=None):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "stream", broken_stream)

    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        assert "connection_error" in resp.text


def test_completions_connect_error_blocking(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)

    def broken_stream(self, method, url, json=None, timeout=None):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "stream", broken_stream)

    with make_client(tmp_path) as client:
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
            headers=_auth_headers(),
        )
        assert resp.status_code == 503


def test_completions_fim_connect_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routers.completions, "COMPLETIONS_API_KEY", TEST_API_KEY)
    fim_data = {"prompt": "def foo():", "model": "llama3.1:latest"}

    def broken_post(self, url, json=None, timeout=None):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", broken_post)

    with make_client(tmp_path) as client:
        resp = client.post("/v1/chat/completions", json=fim_data, headers=_auth_headers())
        assert resp.status_code == 503
