"""Build the verified demo database with real network collection.

This is not a mock. Every article comes from a live HTTP request and its source
URL is stored. Re-running the script never deletes existing articles.
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
    svc = NewsService.create(ROOT / "config", ROOT / "data" / "news.db")
    evidence = {"started_at": datetime.now(UTC).isoformat(timespec="seconds"), "steps": []}
    try:
        for site_id in ("xinhua", "chinanews"):
            report = svc.validate_site(site_id)
            evidence["steps"].append(
                {
                    "operation": "validate_site",
                    "site_id": site_id,
                    "ok": report.get("ok"),
                    "valid_channels": report.get("valid_channels"),
                    "channels": [
                        {
                            "channel_id": c["channel_id"],
                            "ok": c["ok"],
                            "samples": [
                                {
                                    "url": s.get("url"),
                                    "http_status": s.get("http_status"),
                                    "title": s.get("title"),
                                    "published_at_local": s.get("published_at_local"),
                                    "content_chars": s.get("content_chars"),
                                    "extraction_status": s.get("extraction_status"),
                                }
                                for s in c.get("samples", [])
                            ],
                        }
                        for c in report.get("channels", [])
                    ],
                }
            )
            print(f"validated {site_id}: {report.get('valid_channels')} channel(s)")

        for site_id, channel, limit in (
            ("xinhua", "politics", 12),
            ("xinhua", "world", 8),
            ("chinanews", "latest", 10),
        ):
            result = svc.refresh_news(channel, limit, site_id=site_id)
            evidence["steps"].append({"operation": "refresh_news", **result})
            print(
                f"refresh {site_id}/{channel}: discovered={result['discovered_count']} "
                f"inserted={result['inserted_count']} updated={result['updated_count']} "
                f"failed={result['failed_count']} partial={result['partial_count']} "
                f"status={result['status']}"
            )

        # Repeat exactly the same channel to demonstrate URL-based deduplication.
        repeat = svc.refresh_news("politics", 12, site_id="xinhua")
        evidence["steps"].append({"operation": "refresh_news_repeat", **repeat})
        print(
            f"repeat xinhua/politics: discovered={repeat['discovered_count']} "
            f"inserted={repeat['inserted_count']} updated={repeat['updated_count']}"
        )

        evidence["completed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        evidence["article_count"] = svc.db.count_articles()
        evidence["sites"] = svc.list_sites()
        evidence["channels"] = svc.list_channels("all")
        evidence["recent_runs"] = svc.db.recent_runs(limit=20)
        out = ROOT / "evidence" / "dataset_build.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"articles={evidence['article_count']} evidence={out}")
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
