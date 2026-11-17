"""Acceptance audit against the live web and the final database.

Run after scripts/build_dataset.py:
    .venv\\Scripts\\python.exe scripts\\acceptance_audit.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news_mcp.service import NewsService  # noqa: E402
from news_mcp.urls import normalize_url  # noqa: E402

UTC = timezone.utc
BAD_404 = (
    "https://www.news.cn/politics/20261117/"
    "00000000000000000000000000000000/c.html"
)


def main() -> int:
    svc = NewsService.create(ROOT / "config", ROOT / "data" / "news.db")
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "database": str(ROOT / "data" / "news.db"),
        "checks": {},
    }
    try:
        rows = []
        for site_id, channel, wanted in (
            ("xinhua", "politics", 5),
            ("chinanews", "latest", 5),
        ):
            rows.extend(
                [
                    row
                    for row in svc.db.list_latest(
                        site_id=site_id, channel_id=channel, limit=30
                    )
                    if row["extraction_status"] == "ok"
                    and row["content_chars"] >= 80
                ][:wanted]
            )
        samples = []
        for row in rows:
            live = svc.get_article(row["url"], site_id=row["site_id"])
            title_ok = live.get("title") == row["title"]
            date_ok = live.get("published_at_local") == row["published_at_local"]
            url_ok = normalize_url(live.get("url") or "") == normalize_url(row["url"])
            content_ok = len("".join((live.get("content") or "").split())) >= 80
            nav_hits = [
                marker
                for marker in ("分享到", "责任编辑", "相关阅读", "扫一扫", "返回顶部")
                if marker in (live.get("content") or "")
            ]
            samples.append(
                {
                    "site_id": row["site_id"],
                    "url": row["url"],
                    "db_title": row["title"],
                    "live_title": live.get("title"),
                    "db_published_at_local": row["published_at_local"],
                    "live_published_at_local": live.get("published_at_local"),
                    "title_match": title_ok,
                    "date_match": date_ok,
                    "url_match": url_ok,
                    "content_ok": content_ok,
                    "live_status": live.get("status"),
                    "live_http_status": live.get("http_status"),
                    "live_content_chars": live.get("content_chars"),
                    "nav_markers": nav_hits,
                    "nav_clean": not nav_hits,
                }
            )
        report["checks"]["ten_human_verifiable_samples"] = {
            "count": len(samples),
            "title_match_count": sum(1 for s in samples if s["title_match"]),
            "date_match_count": sum(1 for s in samples if s["date_match"]),
            "url_match_count": sum(1 for s in samples if s["url_match"]),
            "content_ok_count": sum(1 for s in samples if s["content_ok"]),
            "nav_clean_count": sum(1 for s in samples if s["nav_clean"]),
            "samples": samples,
            "passed": len(samples) == 10
            and all(
                s["title_match"]
                and s["date_match"]
                and s["url_match"]
                and s["content_ok"]
                and s["nav_clean"]
                for s in samples
            ),
        }

        visible = [
            row
            for row in svc.db.list_latest(site_id=None, limit=100)
            if row["extraction_status"] == "ok" and row["content_chars"] >= 80
        ][:10]
        report["checks"]["ten_visible_body_extractions"] = {
            "count": len(visible),
            "urls": [r["url"] for r in visible],
            "content_chars": [r["content_chars"] for r in visible],
            "passed": len(visible) >= 10,
        }

        # Real-time URL not previously in DB: discover a current link and fetch it.
        initial_count = svc.db.count_articles()
        fresh_inserted = False
        fresh = None
        discovery = svc._adapter(svc.registry.get("xinhua")).discover_articles("politics", 20)
        known = {
            row["url"]
            for row in svc.db.list_latest(site_id="xinhua", limit=100)
        }
        for candidate in discovery.candidates:
            if candidate.url not in known:
                fresh = candidate
                break
        if fresh:
            before = svc.db.count_articles()
            live = svc.get_article(fresh.url, site_id="xinhua")
            after = svc.db.count_articles()
            fresh_inserted = after > before
            report["checks"]["realtime_url_not_previously_saved"] = {
                "url": fresh.url,
                "status": live.get("status"),
                "title": live.get("title"),
                "published_at_local": live.get("published_at_local"),
                "content_chars": live.get("content_chars"),
                "inserted": after > before,
                "passed": live.get("status") in {"ok", "partial"}
                and bool(live.get("title")),
            }
        else:
            report["checks"]["realtime_url_not_previously_saved"] = {
                "passed": False,
                "error": "no undiscovered URL in current listing",
            }

        invalid = svc.get_article("https://example.com/not-a-news-url")
        report["checks"]["invalid_url_rejected"] = {
            "status": invalid.get("status"),
            "error_code": invalid.get("error_code"),
            "passed": invalid.get("status") == "rejected"
            and invalid.get("error_code") == "URL_NOT_ALLOWED",
        }

        not_found = svc.get_article(BAD_404, site_id="xinhua")
        report["checks"]["http_404_status"] = {
            "http_status": not_found.get("http_status"),
            "status": not_found.get("status"),
            "error_code": not_found.get("error_code"),
            "error": not_found.get("error"),
            "passed": not_found.get("status") == "failed"
            and not_found.get("http_status") == 404,
        }

        runs = svc.db.recent_runs(limit=20)
        repeat = [
            r
            for r in runs
            if r["site_id"] == "xinhua" and r["channel_id"] == "politics"
        ]
        report["checks"]["dedupe_and_refresh_audit"] = {
            "total_articles": svc.db.count_articles(),
            "expected_after_realtime_fetch": initial_count + (1 if fresh_inserted else 0),
            "runs": [
                {
                    "run_id": r["run_id"],
                    "started_at": r["started_at"],
                    "attempted": r["attempted_count"],
                    "inserted": r["inserted_count"],
                    "updated": r["updated_count"],
                    "failed": r["failed_count"],
                }
                for r in repeat
            ],
            "passed": svc.db.count_articles() == initial_count + (1 if fresh_inserted else 0)
            and bool(repeat)
            and all(r["inserted_count"] == 0 for r in repeat if r["run_id"] == repeat[0]["run_id"]),
        }

        out = ROOT / "evidence" / "acceptance_audit.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {
            name: {
                k: v
                for k, v in value.items()
                if k in {"count", "passed", "title_match_count", "date_match_count", "url_match_count", "content_ok_count", "total_articles"}
            }
            for name, value in report["checks"].items()
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"evidence={out}")
        return 0 if all(v.get("passed") for v in report["checks"].values()) else 1
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
