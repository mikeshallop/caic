"""Tests for node_agent/agent.py — standalone worker agent."""
import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import node_agent.agent as agent
from node_agent.agent import (
    AgentConfig,
    ModelInfo,
    build_registration_payload,
    discover_models,
    get_load,
    handle_ping,
    handle_swap_model,
)


class FakeMsg:
    def __init__(self, body_dict: dict):
        self.body = json.dumps(body_dict).encode()

    @asynccontextmanager
    async def process(self):
        yield


class FakeExchange:
    def __init__(self, name=""):
        self.name = name
        self.published = []

    async def publish(self, msg, routing_key):
        self.published.append((msg, routing_key))


class FakeChannel:
    def __init__(self):
        self.exchanges = {}
        self.is_closed = False

    async def get_exchange(self, name):
        if name not in self.exchanges:
            self.exchanges[name] = FakeExchange(name)
        return self.exchanges[name]

    async def declare_exchange(self, name, typ, durable=True):
        self.exchanges[name] = FakeExchange(name)
        return self.exchanges[name]

    async def declare_queue(self, name="", exclusive=True):
        return self

    async def bind(self, exchange, routing_key):
        pass

    async def close(self):
        self.is_closed = True


# ── 1. Registration payload shape ────────────────────────────────────


def test_registration_payload_shape():
    cfg = AgentConfig()
    cfg.node_name = "worker01"
    cfg.node_ip = "192.168.50.210"
    cfg.capabilities = ["llm"]
    cfg.active_model = "llama3.1-latest-Q4_K_M.gguf"
    cfg.llama_port = 8081

    inventory = [{"filename": "llama3.1-latest-Q4_K_M.gguf", "name": "llama3.1", "quant": "Q4_K_M"}]
    payload = build_registration_payload(cfg, inventory)

    assert payload["node_name"] == "worker01"
    assert payload["node_type"] == "worker"
    assert payload["ip"] == "192.168.50.210"
    assert payload["capabilities"] == ["llm"]
    assert "active_model" in payload
    assert payload["active_model"]["filename"] == "llama3.1-latest-Q4_K_M.gguf"
    assert payload["active_model"]["port"] == 8081
    assert len(payload["inventory"]) == 1


def test_registration_payload_active_model_none():
    cfg = AgentConfig()
    cfg.active_model = ""
    payload = build_registration_payload(cfg, [])
    assert payload["active_model"] is None


# ── 2. Model discovery ───────────────────────────────────────────────


def test_discover_models(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    # Create valid model files
    (models_dir / "llama3.1-latest-Q4_K_M.gguf").write_text("")
    (models_dir / "mistral-nemo-7b-Q6_K_L.gguf").write_text("")
    # Create file that doesn't match pattern
    (models_dir / "readme.txt").write_text("")
    # Create file with unrecognized naming
    (models_dir / "my_custom_model.gguf").write_text("")

    result = discover_models(str(models_dir))
    assert len(result) == 2
    names = {m["name"] for m in result}
    assert "llama3.1" in names
    assert "mistral-nemo" in names


def test_discover_models_no_directory(tmp_path):
    result = discover_models(str(tmp_path / "nonexistent"))
    assert result == []


# ── 3. Config reading ────────────────────────────────────────────────


def test_config_from_ini(tmp_path):
    ini = tmp_path / "caic-node-agent.conf"
    ini.write_text(
        "[agent]\n"
        "node_name = testnode\n"
        "node_ip = 10.0.0.5\n"
        "capabilities = llm,rag\n"
        "amqp_url = amqp://user:pass@host/vhost\n"
        "llama_port = 9090\n"
        "models_dir = /tmp/models\n"
        "active_model = test.gguf\n"
    )
    cfg = AgentConfig.from_ini(str(ini))
    assert cfg.node_name == "testnode"
    assert cfg.node_ip == "10.0.0.5"
    assert cfg.capabilities == ["llm", "rag"]
    assert cfg.amqp_url == "amqp://user:pass@host/vhost"
    assert cfg.llama_port == 9090
    assert cfg.models_dir == "/tmp/models"
    assert cfg.active_model == "test.gguf"


def test_config_from_ini_missing_uses_defaults(tmp_path):
    cfg = AgentConfig.from_ini(str(tmp_path / "nonexistent.conf"))
    assert cfg.node_name != ""  # socket.gethostname() returns something
    assert cfg.node_type == "worker"
    assert cfg.capabilities == ["llm"]


# ── 4. Ping handler publishes pong ──────────────────────────────────


def test_ping_handler_publishes_pong(monkeypatch):
    monkeypatch.setattr(agent, "HAS_PSUTIL", False)
    monkeypatch.setattr(agent, "HAS_AIO_PIKA", True)

    cfg = AgentConfig()
    cfg.node_name = "testworker"

    channel = FakeChannel()
    admin_ex = FakeExchange("jc.admin")
    channel.exchanges["jc.admin"] = admin_ex
    exchange = admin_ex

    async def run():
        await handle_ping(cfg, channel, exchange, FakeMsg({
            "from": "coordinator",
            "node_name": "testworker",
            "type": "ping",
            "correlation_id": "abc-123",
            "timestamp": "2026-01-01T00:00:00Z",
        }))

    asyncio.run(run())

    assert len(exchange.published) == 1
    msg, rk = exchange.published[0]
    assert rk == "node.testworker.pong"
    payload = json.loads(msg.body)
    assert payload["type"] == "pong"
    assert payload["correlation_id"] == "abc-123"
    assert payload["node_name"] == "testworker"


# ── 5. Model swap success path ──────────────────────────────────────


def test_model_swap_success(monkeypatch):
    monkeypatch.setattr(agent, "HAS_AIO_PIKA", True)
    monkeypatch.setattr(agent, "HAS_HTTPX", True)

    cfg = AgentConfig()
    cfg.node_name = "testworker"
    cfg.llama_port = 9999

    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Mock _wait_for_llama to succeed
    async def fake_wait(*a, **kw):
        return True
    monkeypatch.setattr(agent, "_wait_for_llama", fake_wait)

    # Mock _update_config_active_model to no-op
    monkeypatch.setattr(agent, "_update_config_active_model", lambda c, m: None)

    channel = FakeChannel()
    admin_ex = FakeExchange("jc.admin")
    system_ex = FakeExchange("jc.system")
    channel.exchanges["jc.admin"] = admin_ex
    channel.exchanges["jc.system"] = system_ex

    asyncio.run(handle_swap_model(cfg, channel, (admin_ex, system_ex), FakeMsg({"model_filename": "new-model-Q4_K_M.gguf"})))

    # Check systemctl calls
    assert len(captured_cmds) == 2
    assert captured_cmds[0] == ["systemctl", "stop", "llama-server"]
    assert captured_cmds[1] == ["systemctl", "start", "llama-server"]

    # Check model_ready published on jc.system
    assert len(system_ex.published) == 1
    msg, rk = system_ex.published[0]
    assert rk == "node.testworker.model_ready"
    payload = json.loads(msg.body)
    assert payload["type"] == "model_ready"
    assert payload["active_model"] == "new-model-Q4_K_M.gguf"


# ── 6. Model swap timeout path ──────────────────────────────────────


def test_model_swap_timeout(monkeypatch):
    monkeypatch.setattr(agent, "HAS_AIO_PIKA", True)
    monkeypatch.setattr(agent, "HAS_HTTPX", True)

    cfg = AgentConfig()
    cfg.node_name = "testworker"

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""))

    # Mock _wait_for_llama to fail
    async def fake_wait_fail(*a, **kw):
        return False
    monkeypatch.setattr(agent, "_wait_for_llama", fake_wait_fail)
    monkeypatch.setattr(agent, "_update_config_active_model", lambda c, m: None)

    channel = FakeChannel()
    admin_ex = FakeExchange("jc.admin")
    system_ex = FakeExchange("jc.system")
    channel.exchanges["jc.admin"] = admin_ex
    channel.exchanges["jc.system"] = system_ex

    asyncio.run(handle_swap_model(cfg, channel, (admin_ex, system_ex), FakeMsg({"model_filename": "broken-model-Q4_K_M.gguf"})))

    assert len(system_ex.published) == 1
    msg, rk = system_ex.published[0]
    assert rk == "node.testworker.model_failed"
    payload = json.loads(msg.body)
    assert payload["type"] == "model_failed"
    assert "error" in payload


# ── 7. Load reporting ───────────────────────────────────────────────


def test_get_load_with_psutil(monkeypatch):
    class FakePsutil:
        @staticmethod
        def cpu_percent(interval=0.5):
            return 42.0

        @staticmethod
        def virtual_memory():
            class VM:
                percent = 65.0
            return VM()

    monkeypatch.setattr(agent, "HAS_PSUTIL", True)
    monkeypatch.setattr(agent, "psutil", FakePsutil)

    # Mock subprocess to fail (no rocm-smi)
    def fake_run(*a, **kw):
        raise FileNotFoundError("rocm-smi not found")
    monkeypatch.setattr(subprocess, "run", fake_run)

    load = get_load()
    assert load["cpu_pct"] == 42
    assert load["ram_pct"] == 65
    assert "vram_pct" not in load


def test_get_load_without_psutil(monkeypatch):
    monkeypatch.setattr(agent, "HAS_PSUTIL", False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("")))
    load = get_load()
    assert "cpu_pct" not in load


# ── 8. Agent idle after admission (no heartbeat) ────────────────────


def test_no_heartbeat_timer():
    """Agent has no background heartbeat mechanism. This test asserts that
    the codebase contains no heartbeat-related logic in the agent itself."""
    import inspect
    source = inspect.getsource(agent)
    # There should be no heartbeat timer in the agent
    assert "heartbeat" not in source.lower(), \
        "agent.py should contain no heartbeat logic"


# ── 9. Config writer for model swap ─────────────────────────────────


def test_update_config_active_model(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "CONFIG_PATH", str(tmp_path / "caic-node-agent.conf"))
    cfg = AgentConfig()
    cfg.active_model = "old.gguf"
    agent._update_config_active_model(cfg, "new.gguf")
    assert cfg.active_model == "new.gguf"
    content = (tmp_path / "caic-node-agent.conf").read_text()
    assert "new.gguf" in content


# ── 10. ModelInfo to_dict ────────────────────────────────────────────


def test_model_info_to_dict():
    info = ModelInfo(filename="test-Q4_K_M.gguf", name="test", version="latest", quant="Q4_K_M")
    d = info.to_dict()
    assert d["name"] == "test"
    assert d["quant"] == "Q4_K_M"
    assert d["filename"] == "test-Q4_K_M.gguf"
