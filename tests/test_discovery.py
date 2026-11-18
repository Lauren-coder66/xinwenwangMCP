from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timezone

from news_mcp.config import DiscoveryConfig
from news_mcp.discovery import (
    HtmlDiscovery,
    JsonDiscovery,
    RssDiscovery,
    SitemapDiscovery,
)
from news_mcp.models import FetchResult


class FakeHttp:
    check_dns = False

    def __init__(self, responses: dict[str, FetchResult]):
        self.responses = responses

    def get(self, url: str, **kwargs) -> FetchResult:
        return self.responses.get(url, FetchResult(url=url, error_code="HTTP_404", error="not found"))


def _ok(url: str, text: str, content_type: str = "text/html") -> FetchResult:
    return FetchResult(url=url, final_url=url, status_code=200, text=text, content_type=content_type)


def test_html_discovery_filters_cross_channel_and_deduplicates(xinhua_site):
    channel = xinhua_site.channel("politics")
    assert channel is not None
    listing = """
    <html><body>
      <a href="/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html?utm_source=x">同频道一</a>
      <a href="/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html">重复</a>
      <a href="/world/20261117/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/c.html">跨频道推荐</a>
      <a href="/politics/20261117/cccccccccccccccccccccccccccccccc/c.html">同频道二</a>
      <a href="javascript:void(0)">忽略</a>
    </body></html>"""
    http = FakeHttp({"https://www.news.cn/politics/": _ok("https://www.news.cn/politics/", listing)})
    config = DiscoveryConfig(
        type="html",
        url="https://www.news.cn/politics/",
        url_pattern=r"^/(?:politics|world)/20\d{6}/[0-9a-f]{32}/c\.html$",
    )
    result = HtmlDiscovery(xinhua_site, channel, config).discover(http, 10)
    assert len(result.candidates) == 2
    assert all("/politics/" in c.url for c in result.candidates)
    assert len({c.url for c in result.candidates}) == 2


def test_html_discovery_reports_empty_listing(xinhua_site):
    channel = xinhua_site.channel("politics")
    http = FakeHttp({"https://www.news.cn/politics/": _ok("https://www.news.cn/politics/", "<html></html>")})
    result = HtmlDiscovery(
        xinhua_site,
        channel,
        DiscoveryConfig(type="html", url="https://www.news.cn/politics/"),
    ).discover(http, 10)
    assert result.candidates == []
    assert result.errors[0]["code"] == "NO_ARTICLES_FOUND"


def test_rss_stale_feed_is_rejected(xinhua_site):
    channel = xinhua_site.channel("politics")
    old = format_datetime(datetime(2022, 12, 14, 0, 0, tzinfo=timezone.utc))
    feed = f"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>旧时政</title><link>https://www.news.cn/politics/</link>
      <item><title>旧新闻</title>
      <link>https://www.news.cn/politics/20221214/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html</link>
      <pubDate>{old}</pubDate></item>
    </channel></rss>"""
    http = FakeHttp({"https://www.news.cn/politics/news_politics.xml": _ok("https://www.news.cn/politics/news_politics.xml", feed, "application/rss+xml")})
    result = RssDiscovery(
        xinhua_site,
        channel,
        DiscoveryConfig(
            type="rss",
            url="https://www.news.cn/politics/news_politics.xml",
            max_age_days=7,
        ),
    ).discover(http, 10)
    assert result.candidates == []
    assert result.errors[0]["code"] == "RSS_STALE"


def test_json_discovery_extracts_official_policy_list():
    from news_mcp.config import load_registry
    from pathlib import Path

    registry = load_registry(Path(__file__).resolve().parent.parent / "config")
    gov_site = registry.get("gov_cn")
    assert gov_site is not None
    channel = gov_site.channel("latest_policy")
    assert channel is not None
    payload = """[
      {"TITLE":"政策一","URL":"https://www.gov.cn/zhengce/202611/content_1.htm","DOCRELPUBTIME":"2026-11-15"},
      {"TITLE":"政策二","URL":"https://www.gov.cn/zhengce/content/202611/content_2.htm","DOCRELPUBTIME":"2026-11-10"}
    ]"""
    http = FakeHttp(
        {
            "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json": _ok(
                "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json",
                payload,
                "application/json",
            )
        }
    )
    result = JsonDiscovery(
        gov_site,
        channel,
        DiscoveryConfig(
            type="json",
            url="https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json",
            title_key="TITLE",
            url_key="URL",
            date_key="DOCRELPUBTIME",
            url_pattern=r"^/zhengce/(?:content/)?20\d{4}/content_\d+\.htm$",
        ),
    ).discover(http, 10)
    assert len(result.candidates) == 2
    assert result.candidates[0].title == "政策一"
    assert result.candidates[0].published_at == "2026-11-14T16:00:00+00:00"


def test_sitemap_discovery_extracts_matching_urls(xinhua_site):
    channel = xinhua_site.channel("politics")
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html</loc>
      <lastmod>2026-11-17</lastmod></url>
      <url><loc>https://www.news.cn/world/20261117/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/c.html</loc></url>
    </urlset>"""
    http = FakeHttp({"https://www.news.cn/sitemap.xml": _ok("https://www.news.cn/sitemap.xml", xml, "application/xml")})
    result = SitemapDiscovery(
        xinhua_site,
        channel,
        DiscoveryConfig(type="sitemap", url="https://www.news.cn/sitemap.xml"),
    ).discover(http, 10)
    assert len(result.candidates) == 1
    assert "/politics/" in result.candidates[0].url
