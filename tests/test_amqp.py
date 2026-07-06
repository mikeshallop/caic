import asyncio
import logging

import amqp
from config import AMQP_EXCHANGE_ADMIN


def _reset():
    amqp._connection = None
    amqp._channel = None
    amqp._lock = asyncio.Lock()


class FakeExchange:
    def __init__(self):
        self.messages = []

    async def publish(self, msg, routing_key):
        self.messages.append((msg, routing_key))


class FakeChannel:
    def __init__(self):
        self.is_closed = False
        self.exchanges = {}

    async def close(self):
        self.is_closed = True

    async def declare_exchange(self, name, typ, durable=True):
        self.exchanges[name] = typ

    async def get_exchange(self, name):
        return self.exchanges.setdefault(name, FakeExchange())


class FakeConnection:
    def __init__(self):
        self.is_closed = False
        self._channel = FakeChannel()

    async def channel(self):
        return self._channel

    async def close(self):
        self.is_closed = True


async def fake_connect_robust(url, **_):
    return FakeConnection()


# ---------- tests ----------


def test_publish_success(monkeypatch):
    _reset()
    monkeypatch.setattr(amqp, "HAS_AIO_PIKA", True)
    monkeypatch.setattr("aio_pika.connect_robust", fake_connect_robust)

    asyncio.run(amqp.connect())
    ch = asyncio.run(amqp.get_channel())
    ex = FakeExchange()
    ch.exchanges[AMQP_EXCHANGE_ADMIN] = ex

    asyncio.run(amqp.publish(AMQP_EXCHANGE_ADMIN, "test.key", {"foo": "bar"}))

    assert len(ex.messages) == 1
    msg, rk = ex.messages[0]
    assert rk == "test.key"
    assert msg.body == b'{"foo": "bar"}'


def test_publish_disconnected_no_raise(caplog):
    _reset()
    caplog.set_level(logging.ERROR)
    asyncio.run(amqp.publish(AMQP_EXCHANGE_ADMIN, "test.key", {"x": 1}))

    assert len(caplog.records) > 0
    assert any("cannot publish" in r.message for r in caplog.records)


def test_get_channel_reconnects_when_none(monkeypatch):
    _reset()
    monkeypatch.setattr(amqp, "HAS_AIO_PIKA", True)
    monkeypatch.setattr("aio_pika.connect_robust", fake_connect_robust)

    ch = asyncio.run(amqp.get_channel())
    assert ch is not None
    assert not ch.is_closed
