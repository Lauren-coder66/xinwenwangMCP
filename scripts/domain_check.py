"""Record live status of the domains used by the two adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
URLS = [
    "https://www.news.cn/politics/",
    "https://www.xinhuanet.com/politics/",
    "https://www.news.cn/robots.txt",
    "https://www.xinhuanet.com/robots.txt",
    "https://www.chinanews.com.cn/scroll-news/news1.html",
    "https://www.chinanews.com.cn/robots.txt",
]


def main() -> int:
    records = []
    with httpx.Client(
        timeout=25,
        follow_redirects=True,
        headers={"User-Agent": "news-mcp-domain-check/1.0"},
    ) as client:
        for url in URLS:
            try:
                response = client.get(url)
                records.append(
                    {
                        "url": url,
                        "status_code": response.status_code,
                        "final_url": str(response.url),
                        "content_type": response.headers.get("content-type"),
                        "bytes": len(response.content),
                        "robots_allow_all": "Allow: /" in response.text
                        if url.endswith("robots.txt")
                        else None,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                records.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
    payload = {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "records": records,
        "passed": all(
            r.get("status_code") == 200 and not r.get("error") for r in records
        ),
    }
    out = ROOT / "evidence" / "domains.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
