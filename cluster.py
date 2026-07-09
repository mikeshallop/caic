"""
cAIc — Cluster protocol implementation.
Maintains node registry, event log, coordinator state, and ping-based health checks.
"""
import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timezone

from amqp import publish, subscribe
from config import AMQP_EXCHANGE_ADMIN, AMQP_EXCHANGE_SYSTEM

log = logging.getLogger("caic")

CLUSTER_NODES: dict[str, dict] = {}
CLUSTER_EVENTS: deque = deque(maxlen=1000)
CLUSTER_COORDINATOR: str | None = None
_pending_pings: dict[str, tuple[str, asyncio.Event]] = {}
NODE_NAME: str = "ultron"
PING_TIMEOUT: float = 5.0


def _push_event(category: str, severity: str, node_name: str | None, message: str, details: dict | None = None) -> dict:
    record = {
        "category": category,
        "severity": severity,
        "node": node_name,
        "message": message,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    }
    CLUSTER_EVENTS.append(record)
    level = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}.get(severity, logging.INFO)
    log.log(level, "[cluster] %s: %s", node_name or "system", message)
    return record


async def handle_registration(exchange: str, routing_key: str, payload: dict) -> None:
    global CLUSTER_COORDINATOR
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    node_type = payload.get("node_type", "worker")

    if node_name in CLUSTER_NODES:
        _push_event("cluster", "warn", node_name, "Duplicate registration rejected")
        await publish(AMQP_EXCHANGE_ADMIN, f"node.{node_name}.rejected", {
            "from": NODE_NAME, "node_name": node_name, "type": "rejected",
            "reason": "duplicate_node_name",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        })
        return

    if "node_type" not in payload or "capabilities" not in payload:
        _push_event("cluster", "warn", node_name, "Malformed registration rejected")
        await publish(AMQP_EXCHANGE_ADMIN, f"node.{node_name}.rejected", {
            "from": NODE_NAME, "node_name": node_name, "type": "rejected",
            "reason": "malformed_payload",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        })
        return

    now = datetime.now(timezone.utc).isoformat() + "Z"
    node = {
        "name": node_name,
        "type": node_type,
        "status": "active",
        "ip": payload.get("ip"),
        "capabilities": payload.get("capabilities", []),
        "active_model": payload.get("active_model"),
        "inventory": payload.get("inventory", []),
        "load": payload.get("load"),
        "registered_at": now,
        "last_seen": now,
    }
    CLUSTER_NODES[node_name] = node
    _push_event("cluster", "info", node_name, f"Node registered (type={node_type})")

    await publish(AMQP_EXCHANGE_ADMIN, f"node.{node_name}.admitted", {
        "from": NODE_NAME, "node_name": node_name, "type": "admitted",
        "coordinator": CLUSTER_COORDINATOR,
        "timestamp": now,
    })

    if node_type == "coordinator" and CLUSTER_COORDINATOR is None:
        CLUSTER_COORDINATOR = node_name
        _push_event("cluster", "info", node_name, "Elected as coordinator")
        await publish(AMQP_EXCHANGE_SYSTEM, "cluster.coordinator.response", {
            "from": NODE_NAME, "type": "coord_response",
            "coordinator": node_name, "nodes": list(CLUSTER_NODES.keys()),
            "timestamp": now,
        })


async def handle_deregistration(exchange: str, routing_key: str, payload: dict) -> None:
    global CLUSTER_COORDINATOR
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")

    removed = CLUSTER_NODES.pop(node_name, None)
    if not removed:
        log.warning("[cluster] deregistration for unknown node %s", node_name)
        return

    _push_event("cluster", "info", node_name, "Node deregistered")

    if CLUSTER_COORDINATOR == node_name:
        CLUSTER_COORDINATOR = None
        _push_event("cluster", "warn", node_name, "Coordinator deregistered — no coordinator active")


async def handle_pong(exchange: str, routing_key: str, payload: dict) -> None:
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    correlation_id = payload.get("correlation_id")

    if correlation_id and correlation_id in _pending_pings:
        _, event = _pending_pings.pop(correlation_id)
        event.set()

    if node_name in CLUSTER_NODES:
        now = datetime.now(timezone.utc).isoformat() + "Z"
        CLUSTER_NODES[node_name]["last_seen"] = now
        if "status" in payload:
            CLUSTER_NODES[node_name]["status"] = payload["status"]
        if "active_model" in payload:
            CLUSTER_NODES[node_name]["active_model"] = payload["active_model"]
        if "load" in payload:
            CLUSTER_NODES[node_name]["load"] = payload["load"]
    else:
        log.warning("[cluster] pong from unknown node %s", node_name)


async def handle_event(exchange: str, routing_key: str, payload: dict) -> None:
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    severity = payload.get("severity", "info")
    message = payload.get("message", "")
    details = payload.get("details")
    _push_event("application", severity, node_name, message, details)


async def handle_coordinator_query(exchange: str, routing_key: str, payload: dict) -> None:
    if CLUSTER_COORDINATOR is None:
        return
    now = datetime.now(timezone.utc).isoformat() + "Z"
    await publish(AMQP_EXCHANGE_SYSTEM, "cluster.coordinator.response", {
        "from": NODE_NAME, "type": "coord_response",
        "coordinator": CLUSTER_COORDINATOR,
        "nodes": list(CLUSTER_NODES.keys()),
        "timestamp": now,
    })


async def ping_node(node_name: str) -> bool:
    global CLUSTER_COORDINATOR
    if node_name not in CLUSTER_NODES:
        return False

    correlation_id = str(uuid.uuid4())
    event = asyncio.Event()
    _pending_pings[correlation_id] = (node_name, event)

    now = datetime.now(timezone.utc).isoformat() + "Z"
    await publish(AMQP_EXCHANGE_ADMIN, f"node.{node_name}.ping", {
        "from": NODE_NAME, "node_name": node_name, "type": "ping",
        "correlation_id": correlation_id, "timestamp": now,
    })

    try:
        await asyncio.wait_for(event.wait(), timeout=PING_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        _pending_pings.pop(correlation_id, None)
        CLUSTER_NODES.pop(node_name, None)
        _push_event("cluster", "warn", node_name, "Node unresponsive — deregistered after ping timeout")
        if CLUSTER_COORDINATOR == node_name:
            CLUSTER_COORDINATOR = None
            _push_event("cluster", "warn", node_name, "Coordinator unresponsive — no coordinator active")
        return False


async def request_model_swap(node_name: str, model_filename: str) -> bool:
    """Request a worker node to swap its active model."""
    if node_name not in CLUSTER_NODES:
        log.warning("request_model_swap: unknown node %s", node_name)
        return False

    CLUSTER_NODES[node_name]["status"] = "swapping"
    _push_event("cluster", "info", node_name, f"Model swap requested: {model_filename}")

    now = datetime.now(timezone.utc).isoformat() + "Z"
    await publish(AMQP_EXCHANGE_ADMIN, f"node.{node_name}.cmd.swap_model", {
        "model_filename": model_filename,
        "requested_at": now,
    })
    return True


async def handle_model_ready(exchange: str, routing_key: str, payload: dict) -> None:
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    model_filename = payload.get("active_model")
    port = payload.get("port", 8081)

    if node_name not in CLUSTER_NODES:
        log.warning("handle_model_ready: unknown node %s", node_name)
        return

    inventory = CLUSTER_NODES[node_name].get("inventory") or []
    model_info = None
    for inv in inventory:
        if inv.get("filename") == model_filename:
            model_info = {**inv, "port": port}
            break
    if model_info is None:
        model_info = {"filename": model_filename, "port": port}

    now = datetime.now(timezone.utc).isoformat() + "Z"
    CLUSTER_NODES[node_name]["active_model"] = model_info
    CLUSTER_NODES[node_name]["status"] = "active"
    CLUSTER_NODES[node_name]["last_seen"] = now
    _push_event("cluster", "info", node_name, f"Model swap complete: {model_filename}")


async def handle_heartbeat(exchange: str, routing_key: str, payload: dict) -> None:
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    if node_name not in CLUSTER_NODES:
        log.warning("heartbeat from unknown node %s — ignore, full registration required", node_name)
        return
    now = datetime.now(timezone.utc).isoformat() + "Z"
    CLUSTER_NODES[node_name]["last_seen"] = now


async def handle_model_failed(exchange: str, routing_key: str, payload: dict) -> None:
    node_name = payload.get("node_name", routing_key.split(".")[1] if "." in routing_key else "unknown")
    error = payload.get("error", "unknown error")

    if node_name not in CLUSTER_NODES:
        log.warning("handle_model_failed: unknown node %s", node_name)
        return

    CLUSTER_NODES[node_name]["status"] = "error"
    _push_event("cluster", "error", node_name, f"Model swap failed: {error}")


SUBSCRIBE_TABLE = [
    (AMQP_EXCHANGE_ADMIN, ["node.*.register"], handle_registration),
    (AMQP_EXCHANGE_ADMIN, ["node.*.deregister"], handle_deregistration),
    (AMQP_EXCHANGE_ADMIN, ["node.*.pong"], handle_pong),
    (AMQP_EXCHANGE_SYSTEM, ["node.*.event"], handle_event),
    (AMQP_EXCHANGE_SYSTEM, ["cluster.coordinator.query"], handle_coordinator_query),
    (AMQP_EXCHANGE_SYSTEM, ["node.*.heartbeat"], handle_heartbeat),
    (AMQP_EXCHANGE_SYSTEM, ["node.*.model_ready"], handle_model_ready),
    (AMQP_EXCHANGE_SYSTEM, ["node.*.model_failed"], handle_model_failed),
]


async def start_cluster_subscriptions() -> None:
    for exchange, routing_keys, handler in SUBSCRIBE_TABLE:
        await subscribe(exchange, routing_keys, handler)
    log.info("cluster subscriptions started")
