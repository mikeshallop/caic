"""Tests for cluster.py heartbeat handler."""
import asyncio

import cluster
from config import AMQP_EXCHANGE_SYSTEM


def _reset():
    cluster.CLUSTER_NODES.clear()
    cluster.CLUSTER_EVENTS.clear()
    cluster.CLUSTER_COORDINATOR = None
    cluster._pending_pings.clear()


# ---------- 1. handle_heartbeat() for known node updates last_seen ----------


def test_heartbeat_updates_last_seen(monkeypatch):
    _reset()

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "active",
        "last_seen": "2020-01-01T00:00:00Z",
    }
    original = cluster.CLUSTER_NODES["jarvis"]["last_seen"]

    asyncio.run(cluster.handle_heartbeat(
        AMQP_EXCHANGE_SYSTEM, "node.jarvis.heartbeat",
        {"node_name": "jarvis"},
    ))

    assert cluster.CLUSTER_NODES["jarvis"]["last_seen"] != original
    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "active"  # unchanged


# ---------- 2. handle_heartbeat() for unknown node logs warning, no add ----------


def test_heartbeat_unknown_node_logs_warning(caplog, monkeypatch):
    _reset()
    caplog.set_level("WARNING")

    asyncio.run(cluster.handle_heartbeat(
        AMQP_EXCHANGE_SYSTEM, "node.stranger.heartbeat",
        {"node_name": "stranger"},
    ))

    assert "stranger" not in cluster.CLUSTER_NODES
    assert any("unknown node" in rec.message and "stranger" in rec.message for rec in caplog.records)
