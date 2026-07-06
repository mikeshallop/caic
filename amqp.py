"""
JarvisChat — AMQP connection manager.
Single persistent aio-pika connection with auto-reconnect.
"""
import asyncio
import json
import logging

try:
    import aio_pika
    from aio_pika import DeliveryMode, ExchangeType
    from aio_pika import RobustConnection, RobustChannel
    HAS_AIO_PIKA = True
except ImportError:
    HAS_AIO_PIKA = False

from config import (
    AMQP_RECONNECT_DELAY,
    AMQP_EXCHANGE_ADMIN,
    AMQP_EXCHANGE_SYSTEM,
    get_amqp_url,
)

log = logging.getLogger("jarvischat")

_connection = None
_channel = None
_lock = asyncio.Lock()


async def connect() -> None:
    if not HAS_AIO_PIKA:
        log.warning("aio-pika not installed — AMQP disabled")
        return
    async with _lock:
        global _connection, _channel
        if _connection and not _connection.is_closed:
            return
        url = get_amqp_url()
        log.info("connecting to AMQP broker")
        try:
            conn = await aio_pika.connect_robust(url)
        except Exception as exc:
            log.warning("AMQP connection failed — %s", exc)
            return
        ch = await conn.channel()
        for ex in (AMQP_EXCHANGE_ADMIN, AMQP_EXCHANGE_SYSTEM):
            await ch.declare_exchange(ex, ExchangeType.TOPIC, durable=True)
        _connection = conn
        _channel = ch
        log.info("AMQP connected, exchanges declared")


async def disconnect() -> None:
    if not HAS_AIO_PIKA:
        return
    async with _lock:
        global _connection, _channel
        if _channel and not _channel.is_closed:
            await _channel.close()
        if _connection and not _connection.is_closed:
            await _connection.close()
        _channel = None
        _connection = None
        log.info("AMQP disconnected")


async def get_channel():
    if not HAS_AIO_PIKA:
        return None
    if _channel is None or _channel.is_closed:
        log.warning("AMQP channel missing, attempting reconnect")
        try:
            await connect()
        except Exception:
            log.exception("AMQP reconnect failed")
            return None
    return _channel


async def publish(exchange: str, routing_key: str, payload: dict) -> None:
    if not HAS_AIO_PIKA:
        log.error("cannot publish — aio-pika not installed")
        return
    ch = await get_channel()
    if ch is None:
        log.error("cannot publish — no AMQP channel available")
        return
    try:
        body = json.dumps(payload).encode()
        msg = aio_pika.Message(body, delivery_mode=DeliveryMode.PERSISTENT)
        ex = await ch.get_exchange(exchange)
        await ex.publish(msg, routing_key)
    except Exception:
        log.exception("AMQP publish failed")
