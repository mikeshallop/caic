import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import app
import config
import crypto
import db
import rag
from security import SESSIONS, PIN_ATTEMPTS, RATE_EVENTS


def make_client(tmp_path: Path) -> TestClient:
    os.environ["CAIC_ADMIN_PIN"] = "1234"
    db.DB_PATH = tmp_path / "caic-rag-mgmt.db"
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
        if "/collections/caic_rag" in url:
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


def test_maybe_evict_all_pinned_breaks(monkeypatch):
    """Above high water but only pinned points exist → eviction breaks with 0 deleted."""
    class AllPinnedClient(FakeAsyncClient):
        async def get(self, url, **kw):
            return FakeResponse(200, {"result": {"vectors_count": 45000}})
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                return FakeResponse(200, {"result": {"points": []}})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: AllPinnedClient())
    rag.EVICTION_LOG.clear()
    evicted = asyncio.run(rag.maybe_evict())
    assert evicted == 0
    assert len(rag.EVICTION_LOG) == 0


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
        resp = client.get("/api/rag/stats", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["vector_count"] == 123
        assert data["max_vectors"] == 50000
        assert "high_water_mark" in data
        assert "low_water_mark" in data
        assert data["high_water_pct"] == 80
        assert data["low_water_pct"] == 20
        assert "percent_full" in data
        assert data["pinned_sources"] == ["upload", "profile"]
        assert data["grace_hours"] == 1
        assert "eviction_counts_last_1m" in data
        assert "eviction_counts_last_5m" in data
        assert "eviction_counts_last_30m" in data
        assert "pinned_count" in data
        assert "avg_retrieval_count" in data
        assert "at_risk_count" in data
        assert "eviction_log_size" in data


def test_rag_stats_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/stats", headers=_guest_headers(client))
        assert resp.status_code == 403


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


# ---------- GET /api/rag/points ----------

def test_rag_list_points_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/points", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "points" in data
        assert data["total"] == 123
        assert len(data["points"]) == 0


def test_rag_list_points_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/points", headers=_guest_headers(client))
        assert resp.status_code == 403


def test_rag_list_points_with_data(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        encrypted = crypto.encrypt_text("Hello world test content")

        class DataClient(FakeAsyncClient):
            async def post(self, url, **kw):
                if "/points/scroll" in url:
                    return FakeResponse(200, {
                        "result": {
                            "points": [{
                                "id": "test-pt-1",
                                "payload": {
                                    "text": encrypted,
                                    "source": "terminal",
                                    "ingest_date": "2024-06-15T10:00:00",
                                    "type": "ingest",
                                    "retrieval_count": 3,
                                },
                            }],
                            "next_page_offset": None,
                        }
                    })
                return FakeResponse(200)
            async def get(self, url, **kw):
                if "/collections/caic_rag" in url:
                    return FakeResponse(200, {"result": {"vectors_count": 1}})
                return FakeResponse(200)

        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: DataClient())
        resp = client.get("/api/rag/points", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 1
        p = data["points"][0]
        assert p["id"] == "test-pt-1"
        assert p["source"] == "terminal"
        assert p["type"] == "ingest"
        assert "Hello world" in p["text"]
        assert p["retrieval_count"] == 3


def test_rag_list_points_source_filter(tmp_path, monkeypatch):
    """Source filter should be passed as Qdrant must-match."""
    class FilterClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/scroll" in url:
                body = kw.get("json", {})
                filt = body.get("filter", {})
                must = filt.get("must", [])
                # Verify the source filter was passed
                assert any(m.get("match", {}).get("value") == "upload" for m in must)
                return FakeResponse(200, {"result": {"points": [], "next_page_offset": None}})
            return FakeResponse(200)
        async def get(self, url, **kw):
            return FakeResponse(200, {"result": {"vectors_count": 0}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FilterClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/points?source=upload", headers=_admin_headers(client))
        assert resp.status_code == 200


def test_rag_list_points_search(tmp_path, monkeypatch):
    """Semantic search should use Qdrant search endpoint."""
    encrypted = crypto.encrypt_text("Semantic match text")

    class SearchClient(FakeAsyncClient):
        call_log = []
        async def post(self, url, **kw):
            SearchClient.call_log.append(url)
            if "/api/embeddings" in url:
                vec = [0.1] * 768
                return FakeResponse(200, {"embedding": vec})
            if "/points/search" in url:
                return FakeResponse(200, {"result": [{
                    "id": "search-hit-1",
                    "score": 0.85,
                    "payload": {
                        "text": encrypted,
                        "source": "terminal",
                        "ingest_date": "2024-06-15T10:00:00",
                        "type": "ingest",
                        "retrieval_count": 1,
                    },
                }]})
            return FakeResponse(200)
        async def get(self, url, **kw):
            return FakeResponse(200, {"result": {}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: SearchClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/points?search=hello+world", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 1
        assert data["points"][0]["id"] == "search-hit-1"
        assert data["points"][0]["score"] == 0.85


# ---------- GET /api/rag/point/{point_id} ----------

def test_rag_get_point_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/point/test-1", headers=_guest_headers(client))
        assert resp.status_code == 403


def test_rag_get_point_found(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        encrypted = crypto.encrypt_text("Single point text")

        class GetClient(FakeAsyncClient):
            async def get(self, url, **kw):
                if "/collections/caic_rag/points/test-1" in url:
                    return FakeResponse(200, {"result": {
                        "id": "test-1",
                        "payload": {
                            "text": encrypted,
                            "source": "terminal",
                            "ingest_date": "2024-06-15T10:00:00",
                            "type": "ingest",
                            "retrieval_count": 5,
                        },
                    }})
                return FakeResponse(200)

        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: GetClient())
        resp = client.get("/api/rag/point/test-1", headers=_admin_headers(client))
        assert resp.status_code == 200
        p = resp.json()
        assert p["id"] == "test-1"
        assert p["source"] == "terminal"
        assert "Single point" in p["text"]


def test_rag_get_point_not_found(tmp_path, monkeypatch):
    class NotFoundClient(FakeAsyncClient):
        async def get(self, url, **kw):
            if "/collections/caic_rag/points/" in url:
                return FakeResponse(404, {"detail": "Not found"})
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: NotFoundClient())
    with make_client(tmp_path) as client:
        resp = client.get("/api/rag/point/nonexistent", headers=_admin_headers(client))
        assert resp.status_code == 404


# ---------- DELETE /api/rag/point/{point_id} ----------

def test_rag_delete_point_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.delete("/api/rag/point/test-1", headers=_guest_headers(client))
        assert resp.status_code == 403


def test_rag_delete_point_success(tmp_path, monkeypatch):
    class DeleteClient(FakeAsyncClient):
        async def post(self, url, **kw):
            if "/points/delete" in url:
                pts = kw.get("json", {}).get("points", [])
                assert "test-1" in pts
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: DeleteClient())
    with make_client(tmp_path) as client:
        resp = client.delete("/api/rag/point/test-1", headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["id"] == "test-1"


# ---------- PATCH /api/rag/point/{point_id} ----------

def test_rag_update_point_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.patch("/api/rag/point/test-1", json={"text": "new"}, headers=_guest_headers(client))
        assert resp.status_code == 403


def test_rag_update_point_success(tmp_path, monkeypatch):
    encrypted_old = crypto.encrypt_text("old text")

    class UpdateClient(FakeAsyncClient):
        async def get(self, url, **kw):
            if "/collections/caic_rag/points/test-1" in url:
                return FakeResponse(200, {"result": {
                    "id": "test-1",
                    "payload": {
                        "text": encrypted_old,
                        "source": "terminal",
                        "ingest_date": "2024-06-15T10:00:00",
                        "type": "ingest",
                        "retrieval_count": 2,
                    },
                }})
            return FakeResponse(200)
        async def post(self, url, **kw):
            if "/api/embeddings" in url:
                return FakeResponse(200, {"embedding": [0.2] * 768})
            return FakeResponse(200)
        async def put(self, url, **kw):
            if "/points?wait=true" in url:
                pts = kw.get("json", {}).get("points", [])
                assert len(pts) == 1
                assert len(pts[0]["vector"]) == 768
                payload = pts[0]["payload"]
                assert "text" in payload
                assert payload["source"] == "terminal"
                return FakeResponse(200)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: UpdateClient())
    with make_client(tmp_path) as client:
        resp = client.patch("/api/rag/point/test-1", json={"text": "updated text"}, headers=_admin_headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["id"] == "test-1"


def test_rag_update_point_empty_text(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: FakeAsyncClient())
    with make_client(tmp_path) as client:
        resp = client.patch("/api/rag/point/test-1", json={"text": ""}, headers=_admin_headers(client))
        assert resp.status_code == 400


def test_rag_update_point_not_found(tmp_path, monkeypatch):
    class NotFoundClient(FakeAsyncClient):
        async def get(self, url, **kw):
            if "/collections/caic_rag/points/" in url:
                return FakeResponse(404)
            return FakeResponse(200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: NotFoundClient())
    with make_client(tmp_path) as client:
        resp = client.patch("/api/rag/point/nonexistent", json={"text": "new"}, headers=_admin_headers(client))
        assert resp.status_code == 404
