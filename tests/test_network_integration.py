"""Real-network integration tests.

These are intentionally separate from unit tests. Run them with:
    .venv\\Scripts\\python.exe -m pytest -m network -v
They create a temporary SQLite database and never modify evidence/data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from news_mcp.config import DiscoveryConfig, load_registry
from news_mcp.discovery import RssDiscovery
from news_mcp.fetch import HttpClient
from news_mcp.service import NewsService

pytestmark = pytest.mark.network
ROOT = Path(__file__).resolve().parent.parent


def _service(tmp_path) -> NewsService:
    return NewsService.create(ROOT / "config", tmp_path / "network.db")


def test_xinhua_real_refresh_and_dedupe(tmp_path):
    svc = _service(tmp_path)
    try:
        first = svc.refresh_news("politics", 3)
        assert first["status"] in {"ok", "partial"}
        assert first["attempted_count"] == 3
        assert first["discovered_count"] >= 3
        assert first["inserted_count"] >= 1
        assert svc.db.count_articles() == first["inserted_count"]

        second = svc.refresh_news("politics", 3)
        assert second["inserted_count"] == 0
        assert svc.db.count_articles() == first["inserted_count"]

        latest = svc.get_latest_news("politics", 3)
        assert latest["data_status"] == "local_cache_only"
        assert latest["count"] >= 1
        item = latest["articles"][0]
        assert item["site_id"] == "xinhua"
        assert item["url"].startswith("https://www.news.cn/politics/")
        assert item["published_at"].endswith("+00:00")
        assert item["published_at_local"].endswith("+08:00")

        article = svc.get_article(item["url"])
        assert article["status"] in {"ok", "partial"}
        assert article["title"]
        assert article["data_status"] == "live_http_fetch"
        assert article["content"]
    finally:
        svc.close()


def test_two_real_sites_and_cross_site_search(tmp_path):
    svc = _service(tmp_path)
    try:
        xh = svc.refresh_news("politics", 2)
        cn = svc.refresh_news("latest", 2, site_id="chinanews")
        assert xh["inserted_count"] >= 1
        assert cn["inserted_count"] >= 1

        xh_article = svc.get_latest_news("politics", 1)["articles"][0]
        cn_article = svc.get_latest_news("latest", 1, site_id="chinanews")["articles"][0]
        keyword = xh_article["title"][:2]
        result = svc.search_news(keyword, site_id="all", limit=10)
        assert result["scope"] == "local_collected_articles_only"
        assert any(r["site_id"] == "xinhua" for r in result["results"])

        keyword2 = cn_article["title"][:2]
        result2 = svc.search_news(keyword2, site_id="chinanews", limit=10)
        assert result2["results"]
        assert all(r["site_id"] == "chinanews" for r in result2["results"])
        assert all(r["site_name"] == "中国新闻网" for r in result2["results"])
    finally:
        svc.close()


def test_chinanews_validation_is_real(tmp_path):
    svc = _service(tmp_path)
    try:
        report = svc.validate_site("chinanews")
        assert report["ok"] is True
        assert report["valid_channels"] >= 1
        channels = svc.list_channels("chinanews")["channels"]
        assert channels
        assert channels[0]["source_url"].startswith("https://www.chinanews.com.cn/")
    finally:
        svc.close()


def test_xinhua_rss_is_reachable_but_stale():
    registry = load_registry(ROOT / "config")
    site = registry.get("xinhua")
    channel = site.channel("politics")
    http = HttpClient(site, default_user_agent=registry.settings.user_agent)
    try:
        result = RssDiscovery(
            site,
            channel,
            DiscoveryConfig(
                type="rss",
                url="https://www.news.cn/politics/news_politics.xml",
                max_age_days=7,
            ),
        ).discover(http, 10)
        assert result.candidates == []
        assert result.errors
        assert result.errors[0]["code"] == "RSS_STALE"
        assert result.diagnostics["latest"] < "2023-01-01T00:00:00+00:00"
    finally:
        http.close()
