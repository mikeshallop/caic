import json
import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import config
import db
import routers.chat
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-streaming.db"
    SESSIONS.clear()
    PIN_ATTEMPTS.clear()
    RATE_EVENTS.clear()
    db.init_db()
    return TestClient(app.app, raise_server_exceptions=False)


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


def test_chat_stream_emits_tokens_and_done(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        events = _stream_json_lines(
            [
                {"message": {"content": "Hel"}, "logprobs": [{"logprob": -0.01}]},
                {"message": {"content": "lo"}, "logprobs": [{"logprob": -0.01}]},
                {"done": True, "eval_count": 2, "eval_duration": 1000000000},
            ]
        )

        def stream_stub(self, method, url, json=None, timeout=None):
            return _MockStreamResponse(events)

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

        resp = client.post(
            "/api/chat",
            json={"message": "hello", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        payloads = parse_sse_payloads(resp.text)

        token_text = "".join(p.get("token", "") for p in payloads if "token" in p)
        assert token_text == "Hello"
        done_events = [p for p in payloads if p.get("done")]
        assert done_events
        assert "searched" not in done_events[-1]


def test_chat_auto_search_trigger_emits_search_events(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        first_stream = _stream_json_lines(
            [
                {
                    "message": {"content": "I don't have current data on that question."},
                    "logprobs": [{"logprob": -5.0}],
                },
                {"done": True, "eval_count": 2, "eval_duration": 1000000000},
            ]
        )
        second_stream = _stream_json_lines(
            [
                {"message": {"content": "Based on current data: 42."}},
                {"done": True},
            ]
        )
        stream_batches = [first_stream, second_stream]

        def stream_stub(self, method, url, json=None, timeout=None):
            return _MockStreamResponse(stream_batches.pop(0))

        async def search_stub(query: str, max_results: int = 5):
            return [
                {
                    "title": "Answer",
                    "url": "https://example.com",
                    "content": "The value is 42.",
                }
            ]

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)
        monkeypatch.setattr(routers.chat, "query_searxng", search_stub)

        resp = client.post(
            "/api/chat",
            json={"message": "what is the latest value", "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        payloads = parse_sse_payloads(resp.text)

        assert any(p.get("searching") is True for p in payloads)
        assert any("search_results" in p for p in payloads)
        assert any(p.get("augmented") is True for p in payloads)
        done_events = [p for p in payloads if p.get("done")]
        assert done_events and done_events[-1].get("searched") is True


def test_chat_with_upload_context_id_injects_document(tmp_path: Path, monkeypatch):
    captured_payload = {}

    def stream_stub(self, method, url, json=None, timeout=None):
        nonlocal captured_payload
        captured_payload = json
        events = [{"message": {"content": "ok"}, "logprobs": [{"logprob": -0.01}]}, {"done": True, "eval_count": 1, "eval_duration": 1000000000}]
        return _MockStreamResponse([__import__('json').dumps(e) for e in events])

    monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        db_local = db.get_db()
        expires = "2099-12-31T23:59:59+00:00"
        cid = db.insert_upload_context(db_local, "conv-up", "report.txt", "Confidential document content here", expires, "text/plain")
        db_local.commit()
        db_local.close()

        resp = client.post(
            "/api/chat",
            json={"message": "summarize this", "upload_context_id": cid, "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        system_content = next((m["content"] for m in captured_payload.get("messages", []) if m["role"] == "system"), "")
        assert "Confidential document content here" in system_content


def test_chat_with_expired_upload_context_id_silent(tmp_path: Path, monkeypatch):
    captured_payload = {}

    def stream_stub(self, method, url, json=None, timeout=None):
        nonlocal captured_payload
        captured_payload = json
        events = [{"message": {"content": "ok"}, "logprobs": [{"logprob": -0.01}]}, {"done": True, "eval_count": 1, "eval_duration": 1000000000}]
        return _MockStreamResponse([__import__('json').dumps(e) for e in events])

    monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()["session_id"]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        import datetime
        db_local = db.get_db()
        expires = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
        cid = db.insert_upload_context(db_local, "conv-exp", "old.txt", "Stale data", expires, "text/plain")
        db_local.commit()
        db_local.close()

        resp = client.post(
            "/api/chat",
            json={"message": "hi", "upload_context_id": cid, "model": config.DEFAULT_MODEL},
            headers=headers,
        )
        assert resp.status_code == 200
        system_content = next((m["content"] for m in captured_payload.get("messages", []) if m["role"] == "system"), "")
        assert "Stale data" not in system_content


def test_memory_command_paths_remember_and_forget(tmp_path: Path, monkeypatch):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        base_stream = _stream_json_lines(
            [
                {"message": {"content": "ok"}, "logprobs": [{"logprob": -0.01}]},
                {"done": True, "eval_count": 1, "eval_duration": 1000000000},
            ]
        )

        def stream_stub(self, method, url, json=None, timeout=None):
            return _MockStreamResponse(base_stream)

        monkeypatch.setattr(httpx.AsyncClient, "stream", stream_stub)

        remember_resp = client.post(
            "/api/chat",
            json={
                "message": "remember that my favorite language is rust",
                "model": config.DEFAULT_MODEL,
            },
            headers=headers,
        )
        assert remember_resp.status_code == 200
        remember_events = parse_sse_payloads(remember_resp.text)
        assert any("Remembered" in p.get("token", "") for p in remember_events)

        memories_after_add = client.get("/api/memories", headers={"X-Session-ID": sid, "Origin": "http://testserver"})
        assert memories_after_add.status_code == 200
        assert memories_after_add.json().get("count", 0) >= 1

        forget_resp = client.post(
            "/api/chat",
            json={
                "message": "forget about my favorite language",
                "model": config.DEFAULT_MODEL,
            },
            headers=headers,
        )
        assert forget_resp.status_code == 200
        forget_events = parse_sse_payloads(forget_resp.text)
        assert any("Forgot" in p.get("token", "") for p in forget_events)

        memories_after_forget = client.get("/api/memories", headers={"X-Session-ID": sid, "Origin": "http://testserver"})
        assert memories_after_forget.status_code == 200
        assert memories_after_forget.json().get("count", 0) == 0
