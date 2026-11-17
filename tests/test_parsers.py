from __future__ import annotations

from pathlib import Path

from news_mcp.config import load_registry
from news_mcp.parsers import ArticleParser

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _parser(site_id: str) -> ArticleParser:
    registry = load_registry(ROOT / "config")
    site = registry.get(site_id)
    assert site is not None
    return ArticleParser(site, site.rules_for(site.channels[0].id))


def test_xinhua_parser_extracts_title_date_content_and_removes_ads():
    html = (FIXTURES / "xinhua_article.html").read_text(encoding="utf-8")
    parsed = _parser("xinhua").parse(
        html,
        "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html",
        channel_id="politics",
        fetched_at="2026-11-17T13:00:00+00:00",
    )
    assert parsed.title == "测试文章标题"
    assert parsed.published_at == "2026-11-17T06:41:40+00:00"
    assert parsed.published_at_local == "2026-11-17T14:41:40+08:00"
    assert parsed.source == "新华社"
    assert parsed.extraction_status == "ok"
    assert "第一段正文" in parsed.content
    assert "这是广告" not in parsed.content
    assert "责任编辑" not in parsed.content
    assert "相关推荐" not in parsed.content


def test_xinhua_parser_falls_back_to_title_tag():
    html = """<html><head><title>仅标题标签-新华网</title></head><body>
    <div class="info">2026-11-17 14:41:40</div>
    <div id="detailContent"><p>正文内容。</p></div></body></html>"""
    parsed = _parser("xinhua").parse(
        html,
        "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html",
        channel_id="politics",
        fetched_at="2026-11-17T13:00:00+00:00",
    )
    assert parsed.title == "仅标题标签"
    assert parsed.extraction_status == "partial"  # body shorter than 80 chars


def test_xinhua_parser_short_video_body_is_partial_not_fabricated():
    html = """<html><head><title>视频稿-新华网</title></head><body>
    <div class="info">2026-11-17 11:04:48</div>
    <div id="detailContent"><p>记者：顾天成</p><p>编导：徐中哲</p></div></body></html>"""
    parsed = _parser("xinhua").parse(
        html,
        "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html",
        channel_id="politics",
        fetched_at="2026-11-17T13:00:00+00:00",
    )
    assert parsed.extraction_status == "partial"
    assert "正文过短" in (parsed.extraction_error or "")
    assert parsed.content == "记者：顾天成\n编导：徐中哲"


def test_chinanews_parser_uses_different_selectors_and_date_format():
    html = (FIXTURES / "chinanews_article.html").read_text(encoding="utf-8")
    parsed = _parser("chinanews").parse(
        html,
        "https://www.chinanews.com.cn/gn/2026/11-17/10733764.shtml",
        channel_id="latest",
        fetched_at="2026-11-17T13:00:00+00:00",
    )
    assert parsed.title == "中新网测试标题"
    assert parsed.published_at_local == "2026-11-17T21:37:00+08:00"
    assert parsed.source == "中国新闻网"
    assert parsed.extraction_status == "ok"
    assert "第一段测试正文" in parsed.content
    assert "推荐阅读" not in parsed.content
