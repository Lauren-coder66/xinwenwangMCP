"""Independent official-SDK MCP client: initialize, tools/list, tools/call."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
UTC = timezone.utc


async def main() -> int:
    env = dict(os.environ)
    env.update(
        {
            "NEWS_MCP_DB": str(ROOT / "data" / "news.db"),
            "NEWS_MCP_CONFIG_DIR": str(ROOT / "config"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
    )
    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "news_mcp.server"],
        cwd=str(ROOT),
        env=env,
    )
    record = {"tested_at": datetime.now(UTC).isoformat(timespec="seconds"), "calls": []}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            record["initialize"] = {
                "server": init.server_info.name,
                "version": init.server_info.version,
                "protocol_version": init.protocol_version,
            }
            tools = await session.list_tools()
            record["tools"] = [t.name for t in tools.tools]
            calls = [
                ("list_sites", {}),
                ("list_channels", {"site_id": "all"}),
                ("get_latest_news", {"channel": "latest", "site_id": "chinanews", "limit": 2}),
                ("search_news", {"keyword": "中国", "site_id": "all", "limit": 3}),
                (
                    "get_article",
                    {
                        "url": "https://www.chinanews.com.cn/gj/2026/11-17/10733746.shtml",
                        "site_id": "chinanews",
                    },
                ),
                ("get_article", {"url": "https://example.com/not-news"}),
            ]
            for name, args in calls:
                result = await session.call_tool(name, args)
                payload = result.structured_content
                if payload is None:
                    payload = {"content": [c.model_dump() for c in result.content]}
                record["calls"].append(
                    {"tool": name, "arguments": args, "is_error": result.is_error, "result": payload}
                )
    output = ROOT / "evidence" / "mcp_client_smoke.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "initialize": record["initialize"],
                "tools": record["tools"],
                "calls": [
                    {
                        "tool": c["tool"],
                        "is_error": c["is_error"],
                        "status": c["result"].get("status") if isinstance(c["result"], dict) else None,
                    }
                    for c in record["calls"]
                ],
                "evidence": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
