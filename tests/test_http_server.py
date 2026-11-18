from __future__ import annotations

import httpx
import pytest

from news_mcp.http_server import _split_hosts, create_app


@pytest.mark.asyncio
async def test_health_endpoint_is_public():
    app = create_app(token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "news-mcp"


@pytest.mark.asyncio
async def test_mcp_endpoint_requires_token_when_configured():
    app = create_app(token="secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post("/mcp", json={})
        assert denied.status_code == 401
        assert denied.json()["error"] == "unauthorized"


def test_allowed_hosts_parser():
    assert _split_hosts("example.com, 127.0.0.1:8765 ,") == [
        "example.com",
        "127.0.0.1:8765",
    ]
    assert _split_hosts("") == []
