from __future__ import annotations

from pathlib import Path

from news_mcp.adapters import SiteAdapter
from news_mcp.models import Candidate, DiscoveryResult, FetchResult
from news_mcp.parsers import ArticleParser
from news_mcp.service import NewsService
from news_mcp.storage import Database
from news_mcp.timeutil import now_utc

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (ROOT / "tests" / "fixtures" / "xinhua_article.html").read_text(encoding="utf-8")
URL1 = "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html"
URL2 = "https://www.news.cn/politics/20261117/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/c.html"


class FakeAdapter(SiteAdapter):
    def __init__(self, site, http=None, *, fail_fetch: bool = False):
        super().__init__(site, http)
        self.fail_fetch = fail_fetch
        self.calls = 0

    def discover_articles(self, channel_id: str, limit: int) -> DiscoveryResult:
        candidates = [
            Candidate(URL1, "候选一", channel_id, "html"),
            Candidate(URL2, "候选二", channel_id, "html"),
        ][:limit]
        return DiscoveryResult(candidates, "html", diagnostics={"test": True})

    def fetch_article(self, url: str) -> FetchResult:
        self.calls += 1
        if self.fail_fetch:
            return FetchResult(url=url, error_code="NETWORK_DISABLED", error="网络不可用")
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            text=FIXTURE,
            content_type="text/html",
        )

    def parse_article(self, fetch: FetchResult, *, channel_id: str, candidate=None):
        parser = ArticleParser(self.site, self.site.rules_for(channel_id))
        return parser.parse(
            fetch.text or "",
            fetch.final_url or fetch.url,
            channel_id=channel_id,
            fetched_at=now_utc(),
        )

    def close(self) -> None:
        return None

    def validate_source(self):
        return {
            "validated_at": now_utc(),
            "valid_channels": 1,
            "channels": [
                {
                    "channel_id": "politics",
                    "name": "时政",
                    "url": "https://www.news.cn/politics/",
                    "ok": True,
                    "errors": [],
                    "checks": [],
                    "samples": [],
                }
            ],
        }


def _service(tmp_path, registry):
    db = Database(tmp_path / "news.db")
    service = NewsService(registry, db)
    service.initialize()
    return service


def test_refresh_deduplicates_and_reports_live_data(tmp_path, registry, xinhua_site):
    service = _service(tmp_path, registry)
    adapter = FakeAdapter(xinhua_site)
    service._adapters["xinhua"] = adapter

    first = service.refresh_news("politics", 2)
    assert first["status"] == "ok"
    assert first["attempted_count"] == 2
    assert first["discovered_count"] == 2
    assert first["inserted_count"] == 2
    assert first["failed_count"] == 0
    assert service.db.count_articles() == 2

    second = service.refresh_news("politics", 2)
    assert second["inserted_count"] == 0
    assert second["updated_count"] == 0
    assert service.db.count_articles() == 2

    latest = service.get_latest_news("politics", 2, refresh=True)
    assert latest["data_status"] == "live_refresh_completed"
    assert latest["count"] == 2
    assert all(item["data_status"] == "live" for item in latest["articles"])
    service.close()


def test_refresh_network_failure_is_explicit(tmp_path, registry, xinhua_site):
    service = _service(tmp_path, registry)
    service._adapters["xinhua"] = FakeAdapter(xinhua_site, fail_fetch=True)
    result = service.refresh_news("politics", 2)
    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["attempted_count"] == 2
    assert result["failed_count"] == 2
    assert result["inserted_count"] == 0
    assert any(e.get("code") == "NETWORK_DISABLED" for e in result["errors"])
    service.close()


def test_get_article_rejects_non_whitelist_before_network(tmp_path, registry):
    service = _service(tmp_path, registry)
    result = service.get_article("https://example.com/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html")
    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["error_code"] == "URL_NOT_ALLOWED"
    service.close()


def test_get_article_live_parses_and_persists(tmp_path, registry, xinhua_site):
    service = _service(tmp_path, registry)
    service._adapters["xinhua"] = FakeAdapter(xinhua_site)
    result = service.get_article(URL1)
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["title"] == "测试文章标题"
    assert result["content_chars"] > 80
    assert result["data_status"] == "live_http_fetch"
    assert service.db.count_articles() == 1
    service.close()


def test_validate_site_updates_list_channels(tmp_path, registry, xinhua_site):
    service = _service(tmp_path, registry)
    service._adapters["xinhua"] = FakeAdapter(xinhua_site)
    assert service.list_channels("xinhua")["channels"] == []
    report = service.validate_site("xinhua")
    assert report["ok"] is True
    channels = service.list_channels("xinhua")["channels"]
    assert len(channels) == 1
    assert channels[0]["channel"] == "politics"
    service.close()


def test_search_rejects_empty_keyword_and_bad_dates(tmp_path, registry):
    service = _service(tmp_path, registry)
    assert service.search_news("   ")["error_code"] == "INVALID_KEYWORD"
    assert service.search_news("x", start_date="2026-13-01")["error_code"] == "INVALID_DATE"
    service.close()
