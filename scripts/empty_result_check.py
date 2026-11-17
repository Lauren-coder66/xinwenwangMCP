"""Verify that an empty result is reported as an empty result, not substituted."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news_mcp.service import NewsService  # noqa: E402


def main() -> int:
    svc = NewsService.create(ROOT / "config", ROOT / "data" / "news.db")
    try:
        result = svc.search_news(
            "绝对不存在的关键词_987654321",
            site_id="all",
            limit=10,
        )
        payload = {
            "ok": result.get("ok"),
            "status": result.get("status"),
            "count": result.get("count"),
            "total_matches": result.get("total_matches"),
            "results": result.get("results"),
            "scope": result.get("scope"),
            "passed": result.get("ok") is True
            and result.get("count") == 0
            and result.get("results") == [],
        }
        out = ROOT / "evidence" / "empty_result.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["passed"] else 1
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
