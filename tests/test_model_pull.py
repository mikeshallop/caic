import asyncio

import httpx

from model_pull import ensure_model, _model_available_on_llama, _model_available_on_ollama, _pull_via_ollama


class _MockAsyncResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _MockStreamResponse:
    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self._lines = lines or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def aiter_lines(self):
        class _AIter:
            def __init__(self, lines):
                self._lines = iter(lines)
            def __aiter__(self):
                return self
            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration:
                    raise StopAsyncIteration
        return _AIter(self._lines)


def _mock_models_on_llama(*models):
    async def _get(*args, **kwargs):
        return _MockAsyncResponse(json_data={
            "data": [{"id": m} for m in models]
        })
    return _get


async def _mock_ollama_available(*args, **kwargs):
    return _MockAsyncResponse(status_code=200, json_data={"name": "qwen2.5:latest"})


async def _mock_ollama_unavailable(*args, **kwargs):
    raise httpx.ConnectError("refused")


def _mock_ollama_pull_ok(*args, **kwargs):
    return _MockStreamResponse(200, [
        '{"status": "pulling manifest"}',
        '{"status": "success"}',
    ])


def _mock_ollama_pull_fail(*args, **kwargs):
    return _MockStreamResponse(500, [])


def _mock_ollama_connect_error(*args, **kwargs):
    raise httpx.ConnectError("refused")


def test_available_on_llama(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_models_on_llama("qwen2.5-7b-instruct"))
    assert asyncio.run(_model_available_on_llama("qwen2.5-7b-instruct")) is True
    assert asyncio.run(_model_available_on_llama("nonexistent-model")) is False


def test_available_on_llama_unreachable(monkeypatch):
    async def _connect_error(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx.AsyncClient, "get", _connect_error)
    assert asyncio.run(_model_available_on_llama("qwen2.5-7b-instruct")) is False


def test_available_on_ollama(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_ollama_available)
    assert asyncio.run(_model_available_on_ollama("qwen2.5:latest")) is True


def test_available_on_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_ollama_unavailable)
    assert asyncio.run(_model_available_on_ollama("qwen2.5:latest")) is False


def test_pull_via_ollama_success(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_ollama_pull_ok)
    assert asyncio.run(_pull_via_ollama("qwen2.5:latest")) is True


def test_pull_via_ollama_fail(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_ollama_pull_fail)
    assert asyncio.run(_pull_via_ollama("qwen2.5:latest")) is False


def test_pull_via_ollama_connect_error(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_ollama_connect_error)
    assert asyncio.run(_pull_via_ollama("qwen2.5:latest")) is False


def test_ensure_model_already_on_llama(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "get", _mock_models_on_llama("qwen2.5-7b-instruct"))
    assert asyncio.run(ensure_model("qwen2.5-7b-instruct")) is True


def test_ensure_model_not_on_llama_but_on_ollama(monkeypatch):
    async def _get(*args, **kwargs):
        return _MockAsyncResponse(json_data={"data": []})

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_ollama_available)
    assert asyncio.run(ensure_model("qwen2.5-7b-instruct")) is True


def test_ensure_model_needs_pull(monkeypatch):
    async def _get(*args, **kwargs):
        return _MockAsyncResponse(json_data={"data": []})

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_ollama_unavailable)
    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_ollama_pull_ok)
    assert asyncio.run(ensure_model("qwen2.5-7b-instruct")) is True


def test_ensure_model_pull_fails(monkeypatch):
    async def _get(*args, **kwargs):
        return _MockAsyncResponse(json_data={"data": []})

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_ollama_unavailable)
    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_ollama_pull_fail)
    assert asyncio.run(ensure_model("qwen2.5-7b-instruct")) is False
