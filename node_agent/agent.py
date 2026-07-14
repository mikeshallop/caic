"""
cAIc — Worker node agent.

Standalone AMQP client that registers with the cAIc coordinator,
responds to pings, and handles model swap commands.

## Config file: /etc/caic-node-agent.conf

```ini
[agent]
# hostname — defaults to socket.gethostname()
node_name = jarvis
# LAN IP — defaults from socket
node_ip = 192.168.50.210
# "worker" (fixed)
node_type = worker
# comma-separated capability list
capabilities = llm
# RabbitMQ URL on coordinator
amqp_url = amqp://caic:password@192.168.50.108:5672/caic
# port llama-server listens on
llama_port = 8081
# path to GGUF model files
models_dir = /var/lib/caic/models
# currently active model filename
active_model = llama3.1-latest-Q4_K_M.gguf
```

## systemd unit: /etc/systemd/system/caic-node-agent.service

```ini
[Unit]
Description=cAIc Worker Node Agent
After=network.target rabbitmq.service
Wants=rabbitmq.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/caic
ExecStart=/usr/bin/python3 /opt/caic/node_agent/agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
"""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import aio_pika
    from aio_pika import DeliveryMode, ExchangeType
    HAS_AIO_PIKA = True
except ImportError:
    HAS_AIO_PIKA = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

log = logging.getLogger("caic")
CONFIG_PATH = "/etc/caic-node-agent.conf"

# ── data types ──────────────────────────────────────────────────────────

class AgentConfig:
    def __init__(self):
        self.node_name: str = socket.gethostname()
        self.node_ip: str = "127.0.0.1"
        self.node_type: str = "worker"
        self.capabilities: list[str] = ["llm"]
        self.amqp_url: str = "amqp://caic:password@localhost:5672/caic"
        self.llama_port: int = 8081
        self.models_dir: str = "/var/lib/caic/models"
        self.active_model: str = ""

    @classmethod
    def from_ini(cls, path: str = CONFIG_PATH) -> "AgentConfig":
        cfg = cls()
        parser = ConfigParser()
        if not os.path.exists(path):
            log.warning("config %s not found, using defaults", path)
            return cfg
        parser.read(path)
        sec = "agent"
        if parser.has_section(sec):
            cfg.node_name = parser.get(sec, "node_name", fallback=cfg.node_name)
            cfg.node_ip = parser.get(sec, "node_ip", fallback=cfg.node_ip)
            cfg.node_type = parser.get(sec, "node_type", fallback=cfg.node_type)
            raw_caps = parser.get(sec, "capabilities", fallback="llm")
            cfg.capabilities = [c.strip() for c in raw_caps.split(",") if c.strip()]
            cfg.amqp_url = parser.get(sec, "amqp_url", fallback=cfg.amqp_url)
            cfg.llama_port = parser.getint(sec, "llama_port", fallback=cfg.llama_port)
            cfg.models_dir = parser.get(sec, "models_dir", fallback=cfg.models_dir)
            cfg.active_model = parser.get(sec, "active_model", fallback=cfg.active_model)
        return cfg


class ModelInfo:
    def __init__(self, filename: str, name: str = "", version: str = "", quant: str = ""):
        self.filename = filename
        self.name = name
        self.version = version
        self.quant = quant
        self.path = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "quant": self.quant,
            "filename": self.filename,
        }


# ── model discovery ─────────────────────────────────────────────────────

_MODEL_PATTERN = None  # lazy compile


def discover_models(models_dir: str) -> list[dict]:
    import re
    global _MODEL_PATTERN
    if _MODEL_PATTERN is None:
        _MODEL_PATTERN = re.compile(
            r"^(?P<name>.+?)-(?P<version>[^-]+)-(?P<quant>Q\d+_K_[A-Z]+|IQ\d_[A-Z]+|fp\d+)\.gguf$"
        )
    root = Path(models_dir)
    if not root.is_dir():
        log.warning("models_dir %s not found", models_dir)
        return []

    results = []
    for fpath in sorted(root.glob("*.gguf")):
        m = _MODEL_PATTERN.match(fpath.name)
        if m:
            info = ModelInfo(
                filename=fpath.name,
                name=m.group("name"),
                version=m.group("version"),
                quant=m.group("quant"),
            )
            info.path = str(fpath)
            results.append(info.to_dict())
        else:
            log.debug("skipping unrecognized model filename: %s", fpath.name)
    return results


# ── load reporting ──────────────────────────────────────────────────────

def get_load() -> dict:
    load = {}
    if HAS_PSUTIL:
        load["cpu_pct"] = round(psutil.cpu_percent(interval=0.5))
        load["ram_pct"] = round(psutil.virtual_memory().percent)
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if "VRAM Total" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        total = int(parts[-1])
                elif "VRAM Used" in line:
                    parts = line.split()
                    if len(parts) >= 3:
                        used = int(parts[-1])
            if total and total > 0:
                load["vram_pct"] = round(used / total * 100)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Darwin / Apple Silicon
    if sys.platform == "darwin" and "vram_pct" not in load:
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    m = re.match(r"\s+VRAM \(Dynamic, Max\):\s+(\d+)\s+GB", line)
                    if m:
                        load["vram_pct"] = 50  # unified memory — best guess
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    return load


# ── AMQP helpers ────────────────────────────────────────────────────────

async def declare_exchanges(channel) -> tuple:
    admin = await channel.declare_exchange("jc.admin", ExchangeType.TOPIC, durable=True)
    system = await channel.declare_exchange("jc.system", ExchangeType.TOPIC, durable=True)
    return admin, system


async def publish(channel, exchange, routing_key, payload):
    body = json.dumps(payload).encode()
    msg = aio_pika.Message(body, delivery_mode=DeliveryMode.PERSISTENT)
    await exchange.publish(msg, routing_key)


# ── registration ────────────────────────────────────────────────────────

def build_registration_payload(cfg: AgentConfig, inventory: list[dict]) -> dict:
    model_dict = None
    if cfg.active_model:
        for inv in inventory:
            if inv["filename"] == cfg.active_model:
                model_dict = {**inv, "port": cfg.llama_port}
                break
    return {
        "node_name": cfg.node_name,
        "node_type": cfg.node_type,
        "ip": cfg.node_ip,
        "capabilities": cfg.capabilities,
        "active_model": model_dict,
        "inventory": inventory,
    }


# ── ping / pong ─────────────────────────────────────────────────────────

async def handle_ping(cfg: AgentConfig, channel, exchange, msg: aio_pika.IncomingMessage):
    async with msg.process():
        try:
            payload = json.loads(msg.body.decode())
        except json.JSONDecodeError:
            return
        correlation_id = payload.get("correlation_id")
        if not correlation_id:
            return

        load = get_load()
        now = datetime.now(timezone.utc).isoformat() + "Z"
        pong = {
            "node_name": cfg.node_name,
            "type": "pong",
            "correlation_id": correlation_id,
            "status": "active",
            "active_model": None,  # simplified; could read current
            "load": load,
            "timestamp": now,
        }
        await publish(channel, exchange, f"node.{cfg.node_name}.pong", pong)


# ── model swap ──────────────────────────────────────────────────────────

async def handle_swap_model(cfg: AgentConfig, channel, exchanges, msg: aio_pika.IncomingMessage):
    admin_ex, system_ex = exchanges
    async with msg.process():
        try:
            payload = json.loads(msg.body.decode())
        except json.JSONDecodeError:
            return

        model_filename = payload.get("model_filename")
        if not model_filename:
            log.error("swap_model missing model_filename")
            return

        log.info("swapping model to %s", model_filename)

        # 1. Stop current llama-server
        log.info("stopping llama-server")
        subprocess.run(["systemctl", "stop", "llama-server"], check=False)

        # 2. Update config
        _update_config_active_model(cfg, model_filename)

        # 3. Start llama-server
        log.info("starting llama-server")
        subprocess.run(["systemctl", "start", "llama-server"], check=False)

        # 4. Poll health endpoint
        healthy = await _wait_for_llama(cfg.llama_port, timeout=120, interval=2)

        now = datetime.now(timezone.utc).isoformat() + "Z"
        if healthy:
            result_payload = {
                "node_name": cfg.node_name,
                "type": "model_ready",
                "active_model": model_filename,
                "port": cfg.llama_port,
                "timestamp": now,
            }
            log.info("model swap successful: %s", model_filename)
        else:
            result_payload = {
                "node_name": cfg.node_name,
                "type": "model_failed",
                "active_model": model_filename,
                "port": cfg.llama_port,
                "error": "llama-server did not become healthy within 120s",
                "timestamp": now,
            }
            log.error("model swap failed: %s", model_filename)

        await publish(channel, system_ex, f"node.{cfg.node_name}.{result_payload['type']}", result_payload)


def _update_config_active_model(cfg: AgentConfig, model_filename: str):
    parser = ConfigParser()
    if os.path.exists(CONFIG_PATH):
        parser.read(CONFIG_PATH)
    if not parser.has_section("agent"):
        parser.add_section("agent")
    parser.set("agent", "active_model", model_filename)
    with open(CONFIG_PATH, "w") as f:
        parser.write(f)
    cfg.active_model = model_filename


async def _wait_for_llama(port: int, timeout: int = 120, interval: int = 2) -> bool:
    if not HAS_HTTPX:
        log.warning("httpx not installed, skipping health check")
        return True
    deadline = time.time() + timeout
    url = f"http://localhost:{port}/v1/models"
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            try:
                resp = await client.get(url, timeout=5)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(interval)
    return False


# ── main ────────────────────────────────────────────────────────────────

async def amain():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s caic[%(process)d]: %(levelname)s %(message)s",
    )
    log.info("cAIc node agent starting")

    if not HAS_AIO_PIKA:
        log.error("aio-pika not installed")
        sys.exit(1)

    cfg = AgentConfig.from_ini()
    log.info("node_name=%s node_ip=%s", cfg.node_name, cfg.node_ip)

    inventory = discover_models(cfg.models_dir)
    log.info("discovered %d models", len(inventory))

    # Connect to AMQP
    conn = await aio_pika.connect_robust(cfg.amqp_url)
    channel = await conn.channel()
    admin_ex, system_ex = await declare_exchanges(channel)
    log.info("connected to AMQP broker")

    # Publish registration
    reg_payload = build_registration_payload(cfg, inventory)
    await publish(channel, admin_ex, f"node.{cfg.node_name}.register", reg_payload)
    log.info("registration published")

    # Wait for admission
    response_queue = await channel.declare_queue("", exclusive=True)
    await response_queue.bind(admin_ex, f"node.{cfg.node_name}.admitted")
    await response_queue.bind(admin_ex, f"node.{cfg.node_name}.rejected")

    admitted = False
    async with response_queue.iterator() as iterator:
        async for message in iterator:
            async with message.process():
                payload = json.loads(message.body.decode())
                if payload.get("type") == "admitted":
                    log.info("admitted to cluster")
                    admitted = True
                    break
                else:
                    log.error("rejected: %s", payload.get("reason"))
                    sys.exit(1)

    if not admitted:
        log.error("no admission response received")
        sys.exit(1)

    # Set up ping consumer
    ping_queue = await channel.declare_queue("", exclusive=True)
    await ping_queue.bind(admin_ex, f"node.{cfg.node_name}.ping")
    await ping_queue.consume(lambda msg: handle_ping(cfg, channel, admin_ex, msg))

    # Set up swap consumer
    swap_queue = await channel.declare_queue("", exclusive=True)
    await swap_queue.bind(admin_ex, f"node.{cfg.node_name}.cmd.swap_model")
    await swap_queue.consume(lambda msg: handle_swap_model(cfg, channel, (admin_ex, system_ex), msg))

    log.info("listening for pings and commands")
    # Run forever
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(amain())
