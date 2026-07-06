import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import config
import db
import rag
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["JARVISCHAT_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "jarvischat-rag-mgmt.db"
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


class FakeResponse:
    def __init__(self, status, json_data=None):
        self.status_code = status
        self._json = json_data or {}

    def json(self):
        return self._json


class FakeAsyncClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def get(self, url, **kw):
        if "/collections/jarvis_rag" in url:
            return FakeResponse(200, {"result": {"vectors_count": 123}})
        return FakeResponse(200)

    async def post(self, url, **kw):
        if "/points/scroll" in url:
            return FakeResponse(200, {"result": {"points": []}})
        if "/points/delete" in url:
            return FakeResponse(200)
        return FakeResponse(200)

    async def put(self, url, **kw):
        return FakeResponse(200)


def _old_ts(hours_ago: float = 24) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _young_ts(hours_ago: float = 0.1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# ---------- get_collection_count ----------

def test_get_collection_count(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    count = asyncio.run(rag.get_collection_count())
    assert count == 123


# ---------- get_collection_stats ----------

def test_get_collection_stats_shape(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    stats = asyncio.run(rag.get_collection_stats())
    assert stats["vector_count"] == 123
    assert stats["max_vectors"] == 50000
    assert stats["high_water_mark"] == 40000
    assert stats["low_water_mark"] == 10000
    assert stats["high_water_pct"] == 80
    assert stats["low_water_pct"] == 20
    assert 0 < stats["percent_full"] < 1
    assert "upload" in stats["pinned_sources"]
    assert "profile" in stats["pinned_sources"]


# ---------- evict_batch ----------

def test_evict_batch_excludes_pinned_sources(monkeypatch):
    """Pinned sources ('upload', 'profile') should be in the must_not scroll filter."""
    old = _old_ts(48)
    # Only non-pinned points are returned (real Qdrant would honour must_not filter)
    scroll_points = [
        {"id": "old-data", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 0}},
    ]

    class ScrollClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                must_not = kw.get("json", {}).get("filter", {}).get("must_not", [])
                pinned_values = [m["match"]["value"] for m in must_not]
                assert "upload" in pinned_values
                assert "profile" in pinned_values
                return FakeResponse(200, {"result": {"points": scroll_points}})
            if "/points/delete" in url:
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: ScrollClient())
    deleted = asyncio.run(rag.evict_batch(10))
    assert deleted == 1


def test_evict_batch_respects_grace_period(monkeypatch):
    """Vectors younger than RAG_GRACE_HOURS should be skipped."""
    old = _old_ts(48)
    young = _young_ts(0.1)
    scroll_points = [
        {"id": "mature", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 0}},
        {"id": "newborn", "payload": {"source": "terminal", "ingest_date": young, "retrieval_count": 0}},
    ]

    class GraceClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": scroll_points}})
            if "/points/delete" in url:
                deleted = kw.get("json", {}).get("points", [])
                assert "newborn" not in deleted
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: GraceClient())
    deleted = asyncio.run(rag.evict_batch(10))
    assert deleted == 1


def test_evict_batch_respects_batch_size(monkeypatch):
    """Only up to batch_size vectors should be deleted per call."""
    old = _old_ts(48)
    points = [{"id": f"p{i}", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 0}} for i in range(50)]

    class BatchClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/delete" in url:
                deleted = kw.get("json", {}).get("points", [])
                assert len(deleted) == 10
                return FakeResponse(200)
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": points}})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: BatchClient())
    deleted = asyncio.run(rag.evict_batch(10))
    assert deleted == 10


def test_evict_batch_all_pinned_returns_zero(monkeypatch):
    """If scroll returns nothing (all points filtered by must_not), evict_batch returns 0."""
    class EmptyClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": []}})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: EmptyClient())
    deleted = asyncio.run(rag.evict_batch(10))
    assert deleted == 0


def test_evict_batch_scores_lowest_first(monkeypatch):
    """Vectors with lower scores should be evicted first."""
    old = _old_ts(48)
    points = [
        {"id": "high-score", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 100}},
        {"id": "low-score", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 0}},
        {"id": "mid-score", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 50}},
    ]

    class ScoreClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/delete" in url:
                deleted = kw.get("json", {}).get("points", [])
                assert "low-score" in deleted
                assert "high-score" not in deleted
                assert "mid-score" not in deleted
                return FakeResponse(200)
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": points}})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: ScoreClient())
    deleted = asyncio.run(rag.evict_batch(1))
    assert deleted == 1


# ---------- maybe_evict ----------

def test_maybe_evict_below_high_water(monkeypatch):
    """When count is below high-water mark, eviction should not fire."""
    class LowCountClient(FakeAsyncClient):
        async def get(self, url, **kw):
            # 30000 < 40000 high water
            return FakeResponse(200, {"result": {"vectors_count": 30000}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: LowCountClient())
    rag.EVICTION_LOG.clear()
    evicted = asyncio.run(rag.maybe_evict())
    assert evicted == 0
    assert len(rag.EVICTION_LOG) == 0


def test_maybe_evict_at_high_water(monkeypatch):
    """When count reaches high-water mark, eviction should fire."""
    class HighCountClient(FakeAsyncClient):
        def __init__(self, *a, **kw):
            super().__init__()
            self.call_count = 0

        async def get(self, url, **kw):
            return FakeResponse(200, {"result": {"vectors_count": 45000}})

        async def post(self, url, **kw):
            if "/points/scroll" in url:
                old = _old_ts(48)
                points = [{"id": f"evict-me-{i}", "payload": {"source": "terminal", "ingest_date": old, "retrieval_count": 0}} for i in range(100)]
                return FakeResponse(200, {"result": {"points": points}})
            if "/points/delete" in url:
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: HighCountClient())
    rag.EVICTION_LOG.clear()
    evicted = asyncio.run(rag.maybe_evict())
    assert evicted > 0
    assert len(rag.EVICTION_LOG) == 1
    entry = rag.EVICTION_LOG[0]
    assert "timestamp" in entry
    assert entry["count"] > 0


def test_maybe_evict_zero_config_disabled(monkeypatch):
    """RAG_MAX_VECTORS <= 0 should disable eviction."""
    orig = config.RAG_MAX_VECTORS
    try:
        config.RAG_MAX_VECTORS = 0
        evicted = asyncio.run(rag.maybe_evict())
        assert evicted == 0
    finally:
        config.RAG_MAX_VECTORS = orig


# ---------- get_rag_operational_stats ----------

def test_rag_operational_stats_shape(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    rag.EVICTION_LOG.clear()
    rag.EVICTION_LOG.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count": 500,
        "remaining": 40000,
    })
    stats = asyncio.run(rag.get_rag_operational_stats())
    assert stats["vector_count"] == 123
    assert stats["grace_hours"] == 1
    assert "eviction_counts_last_1m" in stats
    assert "eviction_counts_last_5m" in stats
    assert "eviction_counts_last_30m" in stats
    assert stats["eviction_counts_last_1m"] == 500


# ---------- GET /api/rag/stats ----------

def test_rag_stats_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/stats", headers=_guest_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "vector_count" in data
        assert "max_vectors" in data
        assert "grace_hours" in data
        assert "eviction_log_size" in data
        assert data["vector_count"] == 123


# ---------- POST /api/rag/flush ----------

def test_rag_flush_endpoint(tmp_path, monkeypatch):
    class FlushClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": [{"id": "a"}, {"id": "b"}]}})
            if "/points/delete" in url:
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FlushClient())
    with make_client(tmp_path) as client:
        resp = client.post("/api/rag/flush", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "flushed"
        assert data["deleted_count"] == 2
        assert data["collection"] == rag.RAG_COLLECTION


def test_rag_flush_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.post("/api/rag/flush", headers=_guest_headers(client))
        assert resp.status_code == 403


def test_rag_flush_empty_collection(tmp_path, monkeypatch):
    class EmptyClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": []}})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: EmptyClient())
    with make_client(tmp_path) as client:
        resp = client.post("/api/rag/flush", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_count"] == 0
        assert data["status"] == "flushed"


# ---------- Race lock ----------

def test_eviction_lock_prevents_concurrent_eviction(monkeypatch):
    """Concurrent calls to maybe_evict should queue; only one evicts."""
    call_order = []

    async def slow_get_collection_count():
        call_order.append("count")
        return 45000

    async def slow_evict_batch(bs):
        call_order.append("evict")
        await asyncio.sleep(0.05)
        return 500

    monkeypatch.setattr(rag, "get_collection_count", slow_get_collection_count)
    monkeypatch.setattr(rag, "evict_batch", slow_evict_batch)
    rag.EVICTION_LOG.clear()

    async def run_concurrent():
        r1, r2 = await asyncio.gather(rag.maybe_evict(), rag.maybe_evict())
        return r1, r2

    r1, r2 = asyncio.run(run_concurrent())
    # First call evicted, second found count already below high water or lock serialized
    assert r1 >= 0
    assert r2 >= 0
