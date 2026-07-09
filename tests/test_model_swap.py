"""Tests for cluster.py model swap flow."""
import asyncio

import cluster
import triage
from config import AMQP_EXCHANGE_ADMIN, AMQP_EXCHANGE_SYSTEM


def _reset():
    cluster.CLUSTER_NODES.clear()
    cluster.CLUSTER_EVENTS.clear()
    cluster.CLUSTER_COORDINATOR = None
    cluster._pending_pings.clear()


_published = []


async def _fake_publish(exchange, routing_key, payload):
    _published.append((exchange, routing_key, payload))


# ---------- 1. request_model_swap() publishes swap command ----------


def test_request_model_swap_publishes_command(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "active",
    }

    asyncio.run(cluster.request_model_swap("jarvis", "qwen2.5-coder-Q4_K_M.gguf"))

    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "swapping"
    assert len(_published) == 1
    exchange, rk, payload = _published[0]
    assert exchange == AMQP_EXCHANGE_ADMIN
    assert rk == "node.jarvis.cmd.swap_model"
    assert payload["model_filename"] == "qwen2.5-coder-Q4_K_M.gguf"
    assert "requested_at" in payload


def test_request_model_swap_unknown_node(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    result = asyncio.run(cluster.request_model_swap("ghost", "any.gguf"))
    assert result is False
    assert len(_published) == 0


# ---------- 2. handle_model_ready() updates node ----------


def test_handle_model_ready_updates_active_model(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "swapping",
        "active_model": {"name": "llama3.1", "port": 8081},
        "inventory": [
            {"filename": "qwen2.5-coder-Q4_K_M.gguf", "name": "qwen2.5-coder", "version": "14b", "quant": "Q4_K_M"},
        ],
    }

    asyncio.run(cluster.handle_model_ready(
        AMQP_EXCHANGE_SYSTEM, "node.jarvis.model_ready",
        {"node_name": "jarvis", "active_model": "qwen2.5-coder-Q4_K_M.gguf", "port": 8082},
    ))

    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "active"
    am = cluster.CLUSTER_NODES["jarvis"]["active_model"]
    assert am["name"] == "qwen2.5-coder"
    assert am["port"] == 8082
    assert am["filename"] == "qwen2.5-coder-Q4_K_M.gguf"


def test_handle_model_ready_inventory_lookup_fallback(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "swapping",
        "active_model": {"name": "llama3.1", "port": 8081},
        "inventory": [],
    }

    asyncio.run(cluster.handle_model_ready(
        AMQP_EXCHANGE_SYSTEM, "node.jarvis.model_ready",
        {"node_name": "jarvis", "active_model": "unknown.gguf", "port": 9999},
    ))

    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "active"
    am = cluster.CLUSTER_NODES["jarvis"]["active_model"]
    assert am["filename"] == "unknown.gguf"
    assert am["port"] == 9999


def test_handle_model_ready_unknown_node(caplog, monkeypatch):
    _reset()
    caplog.set_level("WARNING")
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_model_ready(
        AMQP_EXCHANGE_SYSTEM, "node.ghost.model_ready",
        {"node_name": "ghost", "active_model": "any.gguf"},
    ))

    assert any("unknown node" in rec.message for rec in caplog.records)


# ---------- 3. handle_model_failed() sets error status ----------


def test_handle_model_failed_sets_error(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "swapping",
    }

    asyncio.run(cluster.handle_model_failed(
        AMQP_EXCHANGE_SYSTEM, "node.jarvis.model_failed",
        {"node_name": "jarvis", "error": "llama-server unhealthy after 120s"},
    ))

    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "error"
    assert len(cluster.CLUSTER_EVENTS) == 1
    assert "swap failed" in cluster.CLUSTER_EVENTS[0]["message"].lower()


def test_handle_model_failed_unknown_node(caplog, monkeypatch):
    _reset()
    caplog.set_level("WARNING")
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    asyncio.run(cluster.handle_model_failed(
        AMQP_EXCHANGE_SYSTEM, "node.ghost.model_failed",
        {"node_name": "ghost", "error": "OOM"},
    ))

    assert any("unknown node" in rec.message for rec in caplog.records)


# ---------- 4. select_node() triggers swap when model mismatched ----------


def test_select_node_code_triggers_swap(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "active",
        "ip": "192.168.50.210",
        "active_model": {"name": "llama3.1", "port": 8081},
        "inventory": [
            {"filename": "qwen2.5-coder-14b-Q4_K_M.gguf", "name": "qwen2.5-coder", "version": "14b", "quant": "Q4_K_M"},
        ],
    }

    result = asyncio.run(triage.select_node("code"))

    assert result is None
    # Swap should have been published
    assert any("cmd.swap_model" in rk for _, rk, _ in _published)
    # Node should now be swapping
    assert cluster.CLUSTER_NODES["jarvis"]["status"] == "swapping"


# ---------- 5. select_node() returns None when node is already swapping ----------


def test_select_node_swapping_returns_none(monkeypatch):
    _reset()
    _published.clear()
    monkeypatch.setattr(cluster, "publish", _fake_publish)

    cluster.CLUSTER_NODES["jarvis"] = {
        "name": "jarvis", "type": "worker", "status": "swapping",
        "ip": "192.168.50.210",
        "active_model": {"name": "llama3.1", "port": 8081},
        "inventory": [
            {"filename": "qwen2.5-coder-14b-Q4_K_M.gguf", "name": "qwen2.5-coder"},
        ],
    }

    result = asyncio.run(triage.select_node("code"))

    assert result is None
    # No swap command should be published while already swapping
    swap_published = any("cmd.swap_model" in rk for _, rk, _ in _published)
    assert not swap_published
