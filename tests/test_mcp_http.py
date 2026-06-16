from __future__ import annotations

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
