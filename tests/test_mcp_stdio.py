from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
async def test_mcp_stdio_initialize_list_and_invalid_url(tmp_path):
    env = dict(os.environ)
    env.update(
        {
            "NEWS_MCP_DB": str(tmp_path / "mcp.db"),
            "NEWS_MCP_CONFIG_DIR": str(ROOT / "config"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "news_mcp.server"],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info.name == "news-mcp"
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {
                "list_sites",
                "list_channels",
                "get_latest_news",
                "search_news",
                "get_article",
                "refresh_news",
                "inspect_site",
                "validate_site",
            }
            result = await session.call_tool("list_sites", {})
            assert result.is_error is False
            assert result.structured_content["ok"] is True
            assert {s["site_id"] for s in result.structured_content["sites"]} == {
                "xinhua",
                "chinanews",
                "gov_cn",
            }

            rejected = await session.call_tool(
                "get_article", {"url": "https://example.com/not-news"}
            )
            assert rejected.is_error is False
            assert rejected.structured_content["status"] == "rejected"
            assert rejected.structured_content["error_code"] == "URL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_mcp_stdio_rejects_invalid_limit(tmp_path):
    env = dict(os.environ)
    env.update(
        {
            "NEWS_MCP_DB": str(tmp_path / "mcp2.db"),
            "NEWS_MCP_CONFIG_DIR": str(ROOT / "config"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "news_mcp.server"],
        cwd=str(ROOT),
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_latest_news", {"channel": "politics", "limit": 0}
            )
            assert result.is_error is True
