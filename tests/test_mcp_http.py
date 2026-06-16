from __future__ import annotations

import asyncio
import importlib

import pytest
from starlette.testclient import TestClient

from app.config import get_settings
from tests.db_utils import clear_database_caches

pytestmark = pytest.mark.mcp


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    clear_database_caches()


def test_hosted_mcp_http_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    mcp_http = importlib.import_module("app.mcp_http")

    monkeypatch.setenv("MCP_BEARER_TOKEN", "")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="MCP_BEARER_TOKEN"):
        mcp_http.create_app()


def test_hosted_mcp_http_health_and_origin_guard(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://quizzy.example.com")
    get_settings.cache_clear()
    mcp_http = importlib.import_module("app.mcp_http")

    client = TestClient(mcp_http.create_app())

    assert client.get("/health").json() == {"status": "ok", "service": "quizzy-mcp"}
    blocked = client.post(
        "/mcp",
        headers={
            "authorization": "Bearer test-token",
            "origin": "https://evil.example",
            "content-length": "2",
        },
        content="{}",
    )

    assert blocked.status_code == 403


def test_hosted_mcp_http_request_size_guard(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("MCP_MAX_REQUEST_BYTES", "3")
    get_settings.cache_clear()
    mcp_http = importlib.import_module("app.mcp_http")

    client = TestClient(mcp_http.create_app())
    oversized = client.post(
        "/mcp",
        headers={"authorization": "Bearer test-token"},
        content="{}{}",
    )

    assert oversized.status_code == 413


def test_hosted_mcp_http_request_size_guard_limits_stream_without_content_length(
    monkeypatch,
):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")

    response = _guarded_response([b"{}", b"{}"])

    assert response["status"] == 413


def test_hosted_mcp_http_request_size_guard_replays_valid_body(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "test-token")
    response = _guarded_response([b"{}"])

    assert response["status"] == 200
    assert response["body"] == b"2"


def _guarded_response(chunks: list[bytes]) -> dict:
    mcp_http = importlib.import_module("app.mcp_http")
    app = mcp_http.HostedMcpRequestGuard(
        _body_length_app,
        allowed_origins=set(),
        max_request_bytes=3,
    )
    return asyncio.run(_call_asgi_app(app, chunks))


async def _body_length_app(scope, receive, send) -> None:
    body = b""
    more_body = True
    while more_body:
        message = await receive()
        body += message.get("body", b"")
        more_body = message.get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


async def _call_asgi_app(app, chunks: list[bytes]) -> dict:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent_messages = []

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent_messages.append(message)

    await app(
        {"type": "http", "method": "POST", "path": "/mcp", "headers": []},
        receive,
        send,
    )
    return _asgi_response(sent_messages)


def _asgi_response(messages: list[dict]) -> dict:
    response = {"status": None, "body": b""}
    for message in messages:
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
        if message["type"] == "http.response.body":
            response["body"] += message.get("body", b"")
    return response
