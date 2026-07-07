"""Tests for triage.py — query classification and node selection."""
import json

import httpx

import cluster
import config
import triage


def _reset():
    cluster.CLUSTER_NODES.clear()
    cluster.CLUSTER_COORDINATOR = None


class _MockPostResponse:
    def __init__(self, json_data: dict, status_code: int = 200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MockPostContext:
    def __init__(self, response: _MockPostResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


# ---------- 1. classify_query returns valid classification ----------


def test_classify_returns_valid(monkeypatch):
    async def post_stub(self, url, json=None, timeout=None):
        return _MockPostResponse({
            "choices": [{"message": {"content": "code"}}]
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post_stub)

    result = __import__("asyncio").run(triage.classify_query("write a python function"))
    assert result == "code"


# ---------- 2. classify_query on error returns "general" ----------


def test_classify_error_returns_general(monkeypatch):
    async def post_stub(self, url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", post_stub)

    result = __import__("asyncio").run(triage.classify_query("any question"))
    assert result == "general"


# ---------- 3. select_node("code") returns coder node ----------


def test_select_node_code_returns_coder():
    _reset()
    cluster.CLUSTER_NODES["coder01"] = {
        "name": "coder01", "type": "worker", "status": "active",
        "ip": "192.168.50.210",
        "active_model": {"name": "qwen2.5-coder-14b", "port": 8082},
    }
    cluster.CLUSTER_NODES["general01"] = {
        "name": "general01", "type": "worker", "status": "active",
        "ip": "192.168.50.211",
        "active_model": {"name": "llama3.1", "port": 8081},
    }

    node = triage.select_node("code")
    assert node is not None
    assert node["name"] == "coder01"


# ---------- 4. select_node("general") with no matching node returns None ----------


def test_select_node_general_no_match_returns_none():
    _reset()
    cluster.CLUSTER_NODES["coder01"] = {
        "name": "coder01", "type": "worker", "status": "active",
        "active_model": {"name": "qwen2.5-coder-14b", "port": 8082},
    }
    node = triage.select_node("general")
    assert node is None


# ---------- 5. get_inference_url with coder node ----------


def test_get_inference_url_with_coder_node(monkeypatch):
    _reset()
    async def fake_classify(query: str) -> str:
        return "code"
    monkeypatch.setattr(triage, "classify_query", fake_classify)

    cluster.CLUSTER_NODES["coder01"] = {
        "name": "coder01", "type": "worker", "status": "active",
        "ip": "192.168.50.210",
        "active_model": {"name": "qwen2.5-coder-14b", "port": 8082},
    }

    url = __import__("asyncio").run(triage.get_inference_url("write a loop in rust"))
    assert url == "http://192.168.50.210:8082/v1"


# ---------- 6. get_inference_url with no nodes returns LLAMA_SERVER_BASE ----------


def test_get_inference_url_no_nodes(monkeypatch):
    _reset()
    async def fake_classify(query: str) -> str:
        return "code"
    monkeypatch.setattr(triage, "classify_query", fake_classify)

    url = __import__("asyncio").run(triage.get_inference_url("any question"))
    assert url == config.LLAMA_SERVER_BASE
