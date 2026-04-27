import os
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    app_module.DB_PATH = tmp_path / "jarvischat-errors.db"
    app_module.SESSIONS.clear()
    app_module.PIN_ATTEMPTS.clear()
    app_module.RATE_EVENTS.clear()
    app_module.init_db()
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_unhandled_api_exception_returns_friendly_error_with_incident_key(
    tmp_path: Path, monkeypatch
):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        headers = {"X-Session-ID": sid}

        def boom(_topic=None):
            raise RuntimeError("super secret db internals")

        monkeypatch.setattr(app_module, "get_all_memories", boom)

        resp = client.get("/api/memories", headers=headers)
        assert resp.status_code == 500
        payload = resp.json()
        assert payload.get("error_key", "").startswith("INC-")
        assert "support lookup" in payload.get("detail", "").lower()
        assert "super secret db internals" not in resp.text


def test_chat_stream_error_hides_internal_exception_and_emits_incident_key(
    tmp_path: Path, monkeypatch
):
    with make_client(tmp_path) as client:
        sid = client.post("/api/auth/guest", headers={"Origin": "http://testserver"}).json()[
            "session_id"
        ]
        headers = {"X-Session-ID": sid, "Origin": "http://testserver"}

        class BrokenStreamContext:
            async def __aenter__(self):
                raise RuntimeError("ultra secret model transport failure")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        def broken_stream(*args, **kwargs):
            return BrokenStreamContext()

        monkeypatch.setattr(app_module.httpx.AsyncClient, "stream", broken_stream)

        resp = client.post(
            "/api/chat",
            json={"message": "hello", "model": app_module.DEFAULT_MODEL},
            headers=headers,
        )

        assert resp.status_code == 200
        body = resp.text
        assert "ultra secret model transport failure" not in body
        assert "error_key" in body
        assert "support lookup" in body.lower()
