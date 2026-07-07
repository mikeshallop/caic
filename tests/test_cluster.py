"""Tests for cluster.py — no live AMQP, all handlers called directly."""
import asyncio
from collections import deque

import cluster
from config import AMQP_EXCHANGE_ADMIN, AMQP_EXCHANGE_SYSTEM


def _reset():
    cluster.CLUSTER_NODES.clear()
    cluster.CLUSTER_EVENTS.clear()
    cluster.CLUSTER_COORDINATOR = None
    cluster._pending_pings.clear()


_published = []


async def _fake_publish(exchange, routing_key, payload):
    _published.append((exchange, routing_key, payload))


# ---------- 1. Valid worker registration ----------


def test_valid_worker_registration(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis", "node_type": "worker", "capabilities": ["llm"]},
    ))

    assert "jarvis" in cluster.CLUSTER_NODES
    assert cluster.CLUSTER_NODES["jarvis"]["type"] == "worker"
    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "active"
    assert len(cluster.CLUSTER_EVENTS) == 1
    assert cluster.CLUSTER_EVENTS[0]["category"] == "cluster"
    assert cluster.CLUSTER_EVENTS[0]["message"] == "Node registered (type=worker)"

    assert len(_published) == 1
    exchange, rk, payload = _published[0]
    assert exchange == AMQP_EXCHANGE_ADMIN
    assert rk == "node.jarvis.admitted"
    assert payload["type"] == "admitted"
    assert payload["node_name"] == "jarvis"


# ---------- 2. First coordinator auto-promotion ----------


def test_first_coordinator_auto_promotion(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.ultron.register",
        {"node_name": "ultron", "node_type": "coordinator", "capabilities": ["llm", "rag"]},
    ))

    assert cluster.CLUSTER_COORDINATOR == "ultron"
    assert len(cluster.CLUSTER_EVENTS) == 2
    assert cluster.CLUSTER_EVENTS[1]["message"] == "Elected as coordinator"

    assert len(_published) == 2
    # First publish: admitted
    assert _published[0][1] == "node.ultron.admitted"
    # Second publish: coord_response
    exchange, rk, payload = _published[1]
    assert exchange == AMQP_EXCHANGE_SYSTEM
    assert rk == "cluster.coordinator.response"
    assert payload["type"] == "coord_response"
    assert payload["coordinator"] == "ultron"


# ---------- 3. Duplicate node name rejected ----------


def test_duplicate_node_rejected(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis", "node_type": "worker", "capabilities": ["llm"]},
    ))
    _published.clear()

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis", "node_type": "worker", "capabilities": ["llm"]},
    ))

    assert len(cluster.CLUSTER_NODES) == 1
    assert len(_published) == 1
    exchange, rk, payload = _published[0]
    assert exchange == AMQP_EXCHANGE_ADMIN
    assert rk == "node.jarvis.rejected"
    assert payload["reason"] == "duplicate_node_name"


# ---------- 4. Malformed payload rejected ----------


def test_malformed_payload_rejected(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis"},  # missing node_type and capabilities
    ))

    assert len(cluster.CLUSTER_NODES) == 0
    assert len(_published) == 1
    exchange, rk, payload = _published[0]
    assert rk == "node.jarvis.rejected"
    assert payload["reason"] == "malformed_payload"


# ---------- 5. Graceful deregistration ----------


def test_graceful_deregistration(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis", "node_type": "worker", "capabilities": ["llm"]},
    ))
    _published.clear()

    asyncio.run(cluster.handle_deregistration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.deregister",
        {"node_name": "jarvis"},
    ))

    assert "jarvis" not in cluster.CLUSTER_NODES
    assert len(cluster.CLUSTER_EVENTS) == 2
    assert cluster.CLUSTER_EVENTS[1]["message"] == "Node deregistered"


def test_deregister_coordinator_clears(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.ultron.register",
        {"node_name": "ultron", "node_type": "coordinator", "capabilities": ["llm"]},
    ))
    assert cluster.CLUSTER_COORDINATOR == "ultron"
    _published.clear()

    asyncio.run(cluster.handle_deregistration(
        AMQP_EXCHANGE_ADMIN, "node.ultron.deregister",
        {"node_name": "ultron"},
    ))

    assert "ultron" not in cluster.CLUSTER_NODES
    assert cluster.CLUSTER_COORDINATOR is None


# ---------- 6. Pong from known node ----------


def test_pong_updates_known_node(monkeypatch):
    _reset()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.register",
        {"node_name": "jarvis", "node_type": "worker", "capabilities": ["llm"]},
    ))
    original_seen = cluster.CLUSTER_NODES["jarvis"]["last_seen"]

    asyncio.run(cluster.handle_pong(
        AMQP_EXCHANGE_ADMIN, "node.jarvis.pong",
        {"node_name": "jarvis", "status": "busy", "load": {"cpu_pct": 80}},
    ))

    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "busy"
    assert cluster.CLUSTER_NODES["jarvis"]["load"] == {"cpu_pct": 80}
    assert cluster.CLUSTER_NODES["jarvis"]["last_seen"] != original_seen


# ---------- 7. Pong from unknown node ----------


def test_pong_from_unknown_node(caplog, monkeypatch):
    _reset()
    caplog.set_level("WARNING")
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_pong(
        AMQP_EXCHANGE_ADMIN, "node.stranger.pong",
        {"node_name": "stranger", "status": "active"},
    ))

    assert "stranger" not in cluster.CLUSTER_NODES
    assert any("pong from unknown node" in rec.message for rec in caplog.records)


# ---------- 8. Event stored in log ----------


def test_event_appended_to_log(monkeypatch):
    _reset()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_event(
        AMQP_EXCHANGE_SYSTEM, "node.jarvis.event",
        {"node_name": "jarvis", "severity": "error", "message": "OOM on GPU"},
    ))

    assert len(cluster.CLUSTER_EVENTS) == 1
    ev = cluster.CLUSTER_EVENTS[0]
    assert ev["category"] == "application"
    assert ev["severity"] == "error"
    assert ev["node"] == "jarvis"


def test_event_log_bounded(monkeypatch):
    _reset()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    for i in range(1001):
        cluster._push_event("application", "info", "jarvis", f"event {i}")

    assert len(cluster.CLUSTER_EVENTS) == 1000
    assert cluster.CLUSTER_EVENTS[0]["message"] == "event 1"
    assert cluster.CLUSTER_EVENTS[-1]["message"] == "event 1000"


# ---------- 9. Coordinator query produces response ----------


def test_coordinator_query_response(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_registration(
        AMQP_EXCHANGE_ADMIN, "node.ultron.register",
        {"node_name": "ultron", "node_type": "coordinator", "capabilities": ["llm"]},
    ))
    _published.clear()

    asyncio.run(cluster.handle_coordinator_query(
        AMQP_EXCHANGE_SYSTEM, "cluster.coordinator.query",
        {"from": "jarvis", "type": "coord_query"},
    ))

    assert len(_published) == 1
    exchange, rk, payload = _published[0]
    assert exchange == AMQP_EXCHANGE_SYSTEM
    assert rk == "cluster.coordinator.response"
    assert payload["coordinator"] == "ultron"
    assert "nodes" in payload


def test_coordinator_query_no_coordinator(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_coordinator_query(
        AMQP_EXCHANGE_SYSTEM, "cluster.coordinator.query",
        {"from": "jarvis", "type": "coord_query"},
    ))

    assert len(_published) == 0


# ---------- 10. GET /api/cluster shape ----------


def test_cluster_api_shape():
    _reset()
    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "active",
        "capabilities": ["llm"], "active_model": None, "load": None,
        "registered_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
    }
    cluster.CLUSTER_COORDINATOR = "ultron"
    cluster._push_event("cluster", "info", "ultron", "Elected")

    from routers.cluster import cluster_status

    resp = asyncio.run(cluster_status())

    assert "nodes" in resp
    assert "node_count" in resp
    assert "coordinator" in resp
    assert "events" in resp
    assert resp["node_count"] == 1
    assert resp["coordinator"] == "ultron"
    assert "jarvis" in resp["nodes"]
    assert len(resp["events"]) == 1
    # No internal keys leaked
    for node in resp["nodes"].values():
        for key in node:
            assert key in {
                "name", "type", "status", "capabilities", "active_model",
                "load", "registered_at", "last_seen",
            }
