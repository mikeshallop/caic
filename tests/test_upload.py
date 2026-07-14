import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app
import db
import routers.upload as upload_route
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-upload.db"
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


def test_upload_requires_admin(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post("/api/upload", headers=_guest_headers(client), files={"file": ("test.txt", b"hello")})
        assert resp.status_code == 403


def test_upload_unsupported_mime(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/upload", headers=_admin_headers(client),
            files={"file": ("test.exe", b"fake", "application/x-msdownload")},
        )
        assert resp.status_code == 415


def test_upload_context_mode(tmp_path: Path):
    from crypto import decrypt_text
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/upload", headers=_admin_headers(client),
            data={"mode": "context", "conversation_id": "conv-1"},
            files={"file": ("notes.txt", b"Hello world notes")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "notes.txt"
        assert data["mode"] == "context"
        assert "context_id" in data
        assert "chunks_ingested" not in data

        row = db.get_db().execute("SELECT content FROM upload_context WHERE id = ?", (data["context_id"],)).fetchone()
        assert decrypt_text(row["content"]) == "Hello world notes"


def test_upload_ingest_mode(tmp_path: Path, monkeypatch):
    embed_count = 0

    class FakeAsyncClient:
        class FakeResponse:
            def __init__(self, status, json_data=None):
                self.status_code = status
                self._json = json_data or {}

            def json(self):
                return self._json

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            nonlocal embed_count
            if "/api/embeddings" in url:
                embed_count += 1
                return self.FakeResponse(200, {"embedding": [0.1] * 768})
            return self.FakeResponse(200)

        async def put(self, url, **kw):
            return self.FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())

    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/upload", headers=_admin_headers(client),
            data={"mode": "ingest"},
            files={"file": ("data.txt", b"word " * 1000)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "ingest"
        assert data["chunks_ingested"] > 0
        assert "context_id" not in data
        assert embed_count == data["chunks_ingested"]


def test_upload_both_mode(tmp_path: Path, monkeypatch):
    class FakeAsyncClient:
        class FakeResponse:
            def __init__(self, status, json_data=None):
                self.status_code = status
                self._json = json_data or {}

            def json(self):
                return self._json

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            if "/api/embeddings" in url:
                return self.FakeResponse(200, {"embedding": [0.1] * 768})
            return self.FakeResponse(200)

        async def put(self, url, **kw):
            return self.FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())

    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/upload", headers=_admin_headers(client),
            data={"mode": "both", "conversation_id": "conv-2"},
            files={"file": ("both.txt", b"test " * 500)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "both"
        assert "context_id" in data
        assert data["chunks_ingested"] > 0


def test_upload_image_type(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.post(
            "/api/upload", headers=_admin_headers(client),
            data={"mode": "context"},
            files={"file": ("photo.png", b"fake-png", "image/png")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "photo.png"
        assert "context_id" in data


def test_get_upload_by_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp1 = client.post("/api/upload", headers=headers,
                            data={"mode": "context", "conversation_id": "conv-gal"},
                            files={"file": ("a.txt", b"alpha")})
        cid1 = resp1.json()["context_id"]
        resp2 = client.post("/api/upload", headers=headers,
                            data={"mode": "context", "conversation_id": "conv-gal"},
                            files={"file": ("b.txt", b"beta")})
        cid2 = resp2.json()["context_id"]

        gal = client.get("/api/upload/by-conversation/conv-gal", headers=headers)
        assert gal.status_code == 200
        items = gal.json()
        assert len(items) == 2
        assert items[0]["filename"] == "a.txt"
        assert items[1]["filename"] == "b.txt"


def test_link_upload_to_conversation(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.post("/api/upload", headers=headers,
                           data={"mode": "context"},
                           files={"file": ("orphan.txt", b"lonely")})
        cid = resp.json()["context_id"]

        link = client.patch(f"/api/upload/{cid}/link", headers=headers,
                            json={"conversation_id": "new-conv"})
        assert link.status_code == 200

        gal = client.get("/api/upload/by-conversation/new-conv", headers=headers)
        assert len(gal.json()) == 1


def test_delete_upload_removes_context(tmp_path: Path, monkeypatch):
    class FakeAsyncClient:
        class FakeResponse:
            def __init__(self, status, json_data=None):
                self.status_code = status
                self._json = json_data or {}

            def json(self):
                return self._json

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return self.FakeResponse(200, {"result": {"points": [{"id": "upload-test.txt-0"}, {"id": "upload-test.txt-1"}]}})
            if "/points/delete" in url:
                return self.FakeResponse(200)
            return self.FakeResponse(200)

        async def put(self, url, **kw):
            return self.FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())

    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        resp = client.post("/api/upload", headers=headers,
                           data={"mode": "context", "conversation_id": "del-test"},
                           files={"file": ("test.txt", b"delete me")})
        cid = resp.json()["context_id"]

        del_resp = client.delete(f"/api/upload/{cid}", headers=headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "ok"

        row = db.get_db().execute("SELECT id FROM upload_context WHERE id = ?", (cid,)).fetchone()
        assert row is None


def test_delete_upload_not_found(tmp_path: Path):
    with make_client(tmp_path) as client:
        resp = client.delete("/api/upload/999", headers=_admin_headers(client))
        assert resp.status_code == 404


def test_conversation_list_includes_attachment_count(tmp_path: Path):
    with make_client(tmp_path) as client:
        headers = _admin_headers(client)
        client.post("/api/conversations", headers=headers, json={"title": "NoAttach"})
        with_attach = client.post("/api/conversations", headers=headers, json={"title": "WithAttach"}).json()
        conv_id = with_attach["id"]
        client.post("/api/upload", headers=headers,
                    data={"mode": "context", "conversation_id": conv_id},
                    files={"file": ("f.txt", b"data")})

        list_resp = client.get("/api/conversations", headers=headers)
        convs = list_resp.json()
        for c in convs:
            if c["title"] == "NoAttach":
                assert c["attachment_count"] == 0
            if c["title"] == "WithAttach":
                assert c["attachment_count"] == 1
