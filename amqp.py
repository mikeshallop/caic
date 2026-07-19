"""
cAIc — AMQP connection manager.
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

log = logging.getLogger("caic")

_connection = None
_channel = None
_lock = asyncio.Lock()
_subscriptions = []  # list of (exchange, routing_keys, handler_fn)


async def subscribe(exchange: str, routing_keys: list[str], handler) -> None:
    """Subscribe to routing keys on an exchange.

    Creates an exclusive anonymous queue bound to the given routing keys.
    Handler receives (exchange, routing_key, payload_dict).
    On reconnect: _rebind_subscriptions recreates all subscriptions.
    """
    if not HAS_AIO_PIKA:
        log.warning("aio-pika not installed — cannot subscribe")
        return
    ch = await get_channel()
    if ch is None:
        log.error("cannot subscribe — no AMQP channel")
        return
    # Track subscription before attempting so reconnect catches it even if this try fails
    _subscriptions.append((exchange, routing_keys, handler))
    try:
        queue = await ch.declare_queue("", exclusive=True)
        ex = await ch.get_exchange(exchange)
        for rk in routing_keys:
            await queue.bind(ex, rk)

        async def _dispatch(msg: aio_pika.IncomingMessage):
            async with msg.process():
                try:
                    payload = json.loads(msg.body.decode())
                    await handler(exchange, msg.routing_key, payload)
                except Exception:
                    log.exception("AMQP handler error for %s %s", exchange, msg.routing_key)

        await queue.consume(_dispatch)
    except Exception:
        log.exception("AMQP subscribe failed for %s %s", exchange, routing_keys)


async def _rebind_subscriptions() -> None:
    """Re-create all subscriptions after reconnect."""
    subs = list(_subscriptions)
    _subscriptions.clear()
    for exchange, routing_keys, handler in subs:
        await subscribe(exchange, routing_keys, handler)
    if subs:
        log.info("AMQP subscriptions rebound")


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
        await _rebind_subscriptions()
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
