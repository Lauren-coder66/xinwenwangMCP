from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from news_mcp.config import ChannelConfig, ConfigRegistry, Settings, SiteConfig  # noqa: E402
from news_mcp.models import ParsedArticle  # noqa: E402
from news_mcp.timeutil import now_utc  # noqa: E402


@pytest.fixture
def xinhua_site() -> SiteConfig:
    return SiteConfig(
        site_id="xinhua",
        name="新华网",
        homepage="https://www.news.cn/",
        timezone="Asia/Shanghai",
        allowed_domains=["news.cn", "xinhuanet.com"],
        host_aliases={"www.xinhuanet.com": "www.news.cn"},
        article_url_patterns=[
            r"^https://(?:www\.)?(?:news\.cn|xinhuanet\.com)/politics/20\d{6}/[0-9a-f]{32}/c\.html$"
        ],
        request_delay_seconds=0.5,
        channels=[
            ChannelConfig(
                id="politics",
                name="时政",
                url="https://www.news.cn/politics/",
            )
        ],
    )


@pytest.fixture
def registry(xinhua_site: SiteConfig) -> ConfigRegistry:
    return ConfigRegistry({"xinhua": xinhua_site}, Settings(default_site="xinhua"), ROOT)


def _make_parsed(
    *,
    url: str = "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html",
    title: str = "测试新闻标题",
    published_at: str = "2026-11-17T06:41:40+00:00",
    published_at_local: str = "2026-11-17T14:41:40+08:00",
    content: str = "这是测试正文。" * 20,
    status: str = "ok",
    error: str | None = None,
    site_timezone: str = "Asia/Shanghai",
) -> ParsedArticle:
    return ParsedArticle(
        title=title,
        url=url,
        published_at=published_at,
        published_at_local=published_at_local,
        published_date=published_at_local[:10] if published_at_local else None,
        date_precision="second",
        timezone=site_timezone,
        content=content,
        summary="测试摘要",
        author=None,
        source="新华社",
        fetched_at=now_utc(),
        extraction_status=status,
        extraction_error=error,
        http_status=200,
        final_url=url,
        raw_date="2026-11-17 14:41:40",
    )


@pytest.fixture
def make_parsed():
    """Factory fixture so tests can override individual article fields."""
    return _make_parsed
