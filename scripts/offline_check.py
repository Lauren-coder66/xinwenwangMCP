"""Controlled network-failure test. No mock data is returned.

Mode 1: NEWS_MCP_OFFLINE=1 disables all HTTP at the client boundary.
Mode 2: point HTTP(S)_PROXY at a closed local port; httpx then gets a real
connection failure. Run both from the shell and compare the JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news_mcp.service import NewsService  # noqa: E402

UTC = timezone.utc


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "app-offline"
    db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "news.db"
    svc = NewsService.create(ROOT / "config", db_path)
    try:
        before = svc.db.count_articles()
        refresh = svc.refresh_news("politics", 2, site_id="xinhua")
        latest = svc.get_latest_news(
            "politics", 2, refresh=True, site_id="xinhua"
        )
        after = svc.db.count_articles()
        payload = {
            "mode": mode,
            "tested_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "env": {
                "NEWS_MCP_OFFLINE": __import__("os").environ.get("NEWS_MCP_OFFLINE"),
                "HTTPS_PROXY": __import__("os").environ.get("HTTPS_PROXY"),
            },
            "article_count_before": before,
            "article_count_after": after,
            "refresh_status": refresh.get("status"),
            "refresh_attempted": refresh.get("attempted_count"),
            "refresh_failed": refresh.get("failed_count"),
            "refresh_error_codes": sorted(
                {e.get("code") for e in refresh.get("errors", []) if e.get("code")}
            ),
            "refresh_errors": refresh.get("errors"),
            "latest_status": latest.get("status"),
            "latest_data_status": latest.get("data_status"),
            "latest_error": latest.get("error"),
            "latest_refresh_errors": [
                e.get("code")
                for e in (latest.get("refresh") or {}).get("errors", [])
            ],
            "no_articles_inserted": before == after,
        }
        output = ROOT / "evidence" / f"offline_{mode}.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        expected = {"NETWORK_DISABLED"} if mode == "app-offline" else {"FETCH_CONNECT_ERROR"}
        return 0 if payload["no_articles_inserted"] and (
            expected & set(payload["refresh_error_codes"])
        ) else 1
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
