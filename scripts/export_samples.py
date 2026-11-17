"""Export every stored article with its original URL for manual verification."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from news_mcp.service import NewsService  # noqa: E402


def main() -> int:
    svc = NewsService.create(ROOT / "config", ROOT / "data" / "news.db")
    try:
        rows = svc.db.search(
            keyword="",
            start_utc=None,
            end_utc=None,
            site_id=None,
            channel_id=None,
            limit=1000,
        )
        # Empty keyword is not exposed through MCP, but the storage query is
        # useful for a complete local export.
        _, records = rows
        out_dir = ROOT / "evidence"
        out_dir.mkdir(exist_ok=True)
        columns = [
            "site_id",
            "site_name",
            "channel_id",
            "channel_name",
            "title",
            "url",
            "original_url",
            "published_at",
            "published_at_local",
            "source",
            "fetched_at",
            "extraction_status",
            "content_chars",
            "content",
        ]
        csv_path = out_dir / "news_samples.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        json_path = out_dir / "news_samples.json"
        json_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"exported {len(records)} articles -> {csv_path} / {json_path}")
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
