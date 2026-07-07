import json
import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import config
import db
import routers.search_route
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-search-route.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


def _guest_headers(client: TestClient) -> dict:
    sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
    return {"X-Session-ID": sid, "Origin": "http://testserver"}


def parse_sse_payloads(body: str) -> list[dict]:
    payloads: list[dict] = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk.startswith("data: "):
            continue
        raw = chunk[len("data: ") :]
        payloads.append(json.loads(raw))
    return payloads


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


def _stream_json_lines(events: list[dict]) -> list[str]:
    return [json.dumps(event) for event in events]


def test_explicit_search_with_results(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        headers = _guest_headers(client)

        async def search_stub(query: str, max_results: int = 5):
            return [
                {"title": "Result One", "url": "https://example.com/1", "content": "First result content."},
                {"title": "Result Two", "url": "https://example.com/2", "content": "Second result content."},
            ]

        monkeypatch.setattr(routers.search_route, "query_searxng", search_stub)

        events = _stream_json_lines([
            {"choices": [{"delta": {"content": "Here's what I found"}, "logprobs": None}]},
            {"choices": [{"delta": {"content": " about your query."}, "logprobs": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {}},
        ])

        def stream_stub(self, method, url, json=None, timeout=None):
            return _MockStreamResponse(events)

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

        resp = client.post(
            "/api/search",
            json={"query": "current events", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        payloads = parse_sse_payloads(resp.text)

        assert any(p.get("searching") is True for p in payloads)
        assert any("search_results" in p for p in payloads)
        token_text = "".join(p.get("token", "") for p in payloads if "token" in p)
        assert "found" in token_text.lower()
        assert any(p.get("done") and p.get("searched") for p in payloads)


def test_explicit_search_no_results(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        headers = _guest_headers(client)

        async def empty_search(query: str, max_results: int = 5):
            return []

        monkeypatch.setattr(routers.search_route, "query_searxng", empty_search)

        resp = client.post(
            "/api/search",
            json={"query": "nothingness", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        payloads = parse_sse_payloads(resp.text)

        assert any("No search results found" in p.get("token", "") for p in payloads)
        assert any(p.get("done") for p in payloads)
        assert not any("search_results" in p for p in payloads)


def test_explicit_search_new_conversation_created(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        headers = _guest_headers(client)

        async def search_stub(query: str, max_results: int = 5):
            return [{"title": "T", "url": "https://ex.com", "content": "Content."}]

        monkeypatch.setattr(routers.search_route, "query_searxng", search_stub)

        events = _stream_json_lines([
            {"choices": [{"delta": {"content": "Answer."}, "logprobs": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {}},
        ])

        def stream_stub(self, method, url, json=None, timeout=None):
            return _MockStreamResponse(events)

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

        resp = client.post(
            "/api/search",
            json={"query": "tell me something", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        payloads = parse_sse_payloads(resp.text)

        conv_id = None
        for p in payloads:
            if "conversation_id" in p:
                conv_id = p["conversation_id"]
                break
        assert conv_id is not None

        conv_resp = client.get(f"/api/conversations/{conv_id}", headers=_guest_headers(client))
        assert conv_resp.status_code == 200
        data = conv_resp.json()
        assert len(data["messages"]) >= 2


def test_explicit_search_stream_error(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        headers = _guest_headers(client)

        async def search_stub(query: str, max_results: int = 5):
            return [{"title": "T", "url": "https://ex.com", "content": "Content."}]

        monkeypatch.setattr(routers.search_route, "query_searxng", search_stub)

        def broken_stream(self, method, url, json=None, timeout=None):
            class BrokenCtx:
                async def __aenter__(self):
                    raise RuntimeError("summarization failed")
                async def __aexit__(self, exc_type, exc, tb):
                    return False
            return BrokenCtx()

        monkeypatch.setattr(httpx.AsyncClient, "stream", broken_stream)

        resp = client.post(
            "/api/search",
            json={"query": "breaking news", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "error_key" in resp.text
        assert "INC-" in resp.text
