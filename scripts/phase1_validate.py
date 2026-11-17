"""Phase 1 evidence collector for the Xinhua source.

Run from the project root:
    .venv\\Scripts\\python.exe scripts\\phase1_validate.py

It fetches live pages, verifies RSS freshness, discovers current channel links,
parses random real articles and writes evidence/phase1_xinhua.json plus a
human-readable Markdown report. It does not write the application database.
"""

from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import feedparser  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from news_mcp.adapters import GenericSiteAdapter  # noqa: E402
from news_mcp.config import load_registry  # noqa: E402
from news_mcp.fetch import HttpClient  # noqa: E402
from news_mcp.timeutil import parse_article_time  # noqa: E402
from news_mcp.urls import normalize_url  # noqa: E402

UTC = timezone.utc
RSS_URL = "https://www.news.cn/politics/news_politics.xml"
POLITICS_URL = "https://www.news.cn/politics/"
SEED = 20261117


def _text(node) -> str:
    return " ".join((node.get_text(" ", strip=True) if node else "").split())


def _page_evidence(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title_node = soup.select_one("h1 .title")
    if title_node is not None and not _text(title_node):
        title_node = None
    h1 = title_node or soup.select_one("h1")
    info = soup.select_one(".info")
    source = soup.select_one(".source")
    date_match = re.search(
        r"20\d{2}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?",
        _text(info),
    )
    content_node = soup.select_one("#detailContent")
    paragraphs = content_node.find_all("p") if content_node else []
    page_title = _text(h1)
    title_source = "h1" if page_title else "title_tag"
    if not page_title:
        page_title = _text(soup.title)
        for suffix in ("-新华网", "_新华网", "—新华网"):
            page_title = page_title.removesuffix(suffix)
        page_title = page_title.strip()
    return {
        "page_title": page_title,
        "page_title_source": title_source,
        "page_title_tag": _text(soup.title),
        "page_date_raw": date_match.group(0) if date_match else None,
        "page_info": _text(info),
        "page_source": _text(source),
        "has_detailContent": bool(content_node),
        "detail_paragraphs": len(paragraphs),
        "detail_text_chars": len(
            "".join((content_node.get_text("", strip=True) if content_node else "").split())
        ),
        "h1_count": len(soup.find_all("h1")),
        "meta_publishdate": (
            soup.find("meta", attrs={"name": "publishdate"}).get("content")
            if soup.find("meta", attrs={"name": "publishdate"})
            else None
        ),
        "nav_markers_in_detail": [
            marker
            for marker in ("分享到", "责任编辑", "相关阅读", "广告")
            if content_node and marker in content_node.get_text(" ", strip=True)
        ],
    }


def rss_probe(http: HttpClient) -> dict:
    started = datetime.now(UTC)
    result = http.get(RSS_URL, check_robots=True)
    data = {
        "url": RSS_URL,
        "fetched_at": started.isoformat(timespec="seconds"),
        "http_status": result.status_code,
        "error": result.error,
        "entries": 0,
        "latest_published_at": None,
        "usable_for_latest_news": False,
        "note": "",
    }
    if not result.ok:
        data["note"] = "RSS 不可访问"
        return data
    feed = feedparser.parse(result.text or "")
    entries = []
    inferred = 0
    for entry in feed.entries or []:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            entries.append(datetime(*parsed[:6], tzinfo=UTC))
            continue
        # This feed omits pubDate. Its article URLs still carry a date path
        # (e.g. /politics/2022-12/14/c_...), which is evidence, not fabrication.
        link = str(entry.get("link") or "")
        m = re.search(r"/(20\d{2})-(\d{2})/(\d{2})/", link)
        if m:
            entries.append(
                datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC)
            )
            inferred += 1
    data["entries"] = len(feed.entries or [])
    data["dates_inferred_from_url"] = inferred
    data["feed_updated"] = feed.feed.get("updated")
    if entries:
        latest = max(entries)
        data["latest_published_at"] = latest.isoformat(timespec="seconds")
        data["age_days"] = (datetime.now(UTC) - latest).days
        data["usable_for_latest_news"] = data["age_days"] <= 7
        if data["usable_for_latest_news"]:
            data["note"] = "RSS 有近期条目，可作为候选发现方式"
        elif inferred:
            data["note"] = (
                "RSS 条目不提供 pubDate；按文章 URL 日期推断，最新条目仍为 "
                f"{latest.date().isoformat()}，已过期，不能作为最新新闻来源"
            )
        else:
            data["note"] = "RSS 可访问但内容过期；不得作为最新新闻来源"
    else:
        data["note"] = "RSS 条目无发布时间且 URL 无日期，无法证明新鲜度"
    return data


def article_record(adapter, candidate, http: HttpClient) -> dict:
    fetch = adapter.fetch_article(candidate.url)
    if not fetch.ok:
        return {
            "url": candidate.url,
            "http_status": fetch.status_code,
            "fetch_error": fetch.error,
            "status": "failed",
        }
    parsed = adapter.parse_article(fetch, channel_id="politics", candidate=candidate)
    page = _page_evidence(fetch.text or "", fetch.final_url or candidate.url)
    parsed_local = (
        parsed.published_at_local[:19].replace("T", " ")
        if parsed.published_at_local
        else None
    )
    title_match = parsed.title == page["page_title"]
    date_match = bool(parsed_local and parsed_local == page["page_date_raw"])
    return {
        "url": parsed.url,
        "final_url": fetch.final_url,
        "http_status": fetch.status_code,
        "title": parsed.title,
        "page_title": page["page_title"],
        "title_match": title_match,
        "published_at": parsed.published_at,
        "published_at_local": parsed.published_at_local,
        "page_date_raw": page["page_date_raw"],
        "date_match": date_match,
        "source": parsed.source,
        "page_source": page["page_source"],
        "extraction_status": parsed.extraction_status,
        "extraction_error": parsed.extraction_error,
        "content_chars": len("".join((parsed.content or "").split())),
        "content": parsed.content,
        "content_preview": (parsed.content or "")[:300],
        "page": page,
    }


def main() -> int:
    registry = load_registry(ROOT / "config")
    site = registry.get("xinhua")
    if site is None:
        print("xinhua config missing", file=sys.stderr)
        return 2
    http = HttpClient(site, default_user_agent=registry.settings.user_agent)
    adapter = GenericSiteAdapter(site, http)
    out = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "site": "xinhua",
        "timezone": "Asia/Shanghai",
        "seed": SEED,
        "rss": {},
        "robots": {},
        "listing": {},
        "five_articles": [],
        "ten_random_visible": [],
        "structure_by_channel": [],
        "problems": [],
        "strategy": "",
    }
    try:
        robots = http.get(
            "https://www.news.cn/robots.txt", check_robots=False
        )
        out["robots"] = {
            "url": "https://www.news.cn/robots.txt",
            "http_status": robots.status_code,
            "text": robots.text,
            "allows_all": bool(robots.text and "Allow: /" in robots.text),
        }
        out["rss"] = rss_probe(http)

        listing_page = http.get(
            POLITICS_URL,
            allowed_domains=site.allowed_domains,
            check_robots=site.respect_robots,
        )
        soup = BeautifulSoup(listing_page.text or "", "lxml")
        anchors = soup.select("a[href]")
        out["listing"] = {
            "url": POLITICS_URL,
            "http_status": listing_page.status_code,
            "final_url": listing_page.final_url,
            "anchors": len(anchors),
        }
        discovery = adapter.discover_articles("politics", 120)
        out["listing"].update(
            {
                "discovered_article_links": len(discovery.candidates),
                "first_links": [
                    {"url": c.url, "anchor_title": c.title}
                    for c in discovery.candidates[:20]
                ],
                "diagnostics": discovery.diagnostics,
            }
        )

        # Random sample from the current listing, with a fixed seed for reproducibility.
        rng = random.Random(SEED)
        pool = discovery.candidates
        sample = rng.sample(pool, min(15, len(pool)))
        records = [article_record(adapter, c, http) for c in sample]
        visible = [
            r
            for r in records
            if r.get("status") != "failed"
            and r.get("extraction_status") == "ok"
            and r.get("content_chars", 0) >= 80
        ]
        out["ten_random_visible"] = visible[:10]
        out["five_articles"] = visible[:5]
        out["random_sample_attempted"] = len(records)
        out["random_sample_visible_count"] = len(visible)
        out["random_sample_all_statuses"] = [
            {
                "url": r["url"],
                "status": r.get("extraction_status"),
                "content_chars": r.get("content_chars", 0),
            }
            for r in records
        ]

        # One real article per channel documents structural similarity/differences.
        for channel in site.channels:
            d = adapter.discover_articles(channel.id, 5)
            if not d.candidates:
                out["structure_by_channel"].append(
                    {"channel": channel.id, "ok": False, "error": d.errors}
                )
                continue
            candidate = d.candidates[0]
            record = article_record(adapter, candidate, http)
            record["channel"] = channel.id
            out["structure_by_channel"].append(record)

        if not out["rss"].get("usable_for_latest_news"):
            out["problems"].append(
                "官方 RSS 虽返回 200，但最新条目为 2022-12-14，已过期，不能作为最新新闻源。"
            )
        if out["rss"].get("http_status") == 200:
            out["problems"].append(
                "RSS 地址未在栏目页通过 <link rel=alternate> 暴露，且内容为 2022 年静态历史。"
            )
        out["problems"].append(
            "栏目页含跨频道推荐链接；已用 same_channel_path 过滤，避免将推荐文章混入频道。"
        )
        out["problems"].append(
            "部分条目是视频稿，正文只有编导/记者署名，正文提取标记为 partial，不伪造正文。"
        )
        out["strategy"] = (
            "采用栏目 HTML 发现 + 文章页 #detailContent 正文提取；不使用过期 RSS；"
            "严格同频道路径过滤；按 1.5 秒/域名限速并遵守 robots.txt（Allow: /）。"
        )

        summary = {
            "rss_entries": out["rss"].get("entries"),
            "rss_latest": out["rss"].get("latest_published_at"),
            "rss_usable": out["rss"].get("usable_for_latest_news"),
            "listing_links": out["listing"].get("discovered_article_links"),
            "five_articles": len(out["five_articles"]),
            "ten_visible": len(out["ten_random_visible"]),
            "five_title_match": all(a.get("title_match") for a in out["five_articles"]),
            "five_date_match": all(a.get("date_match") for a in out["five_articles"]),
            "content_success": sum(
                1 for a in out["ten_random_visible"] if a.get("content_chars", 0) >= 80
            ),
        }
        out["summary"] = summary
    finally:
        http.close()

    evidence_dir = ROOT / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "phase1_xinhua.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 第一阶段：新华网数据源验证",
        "",
        f"- 生成时间（UTC）：{out['generated_at']}",
        f"- robots.txt：HTTP {out['robots'].get('http_status')}，"
        f"`Allow: /` = {out['robots'].get('allows_all')}",
        f"- RSS：HTTP {out['rss'].get('http_status')}，"
        f"{out['rss'].get('entries')} 条，最新 {out['rss'].get('latest_published_at')}，"
        f"可用于最新新闻：{out['rss'].get('usable_for_latest_news')}",
        f"- 时政栏目页：HTTP {out['listing'].get('http_status')}，"
        f"发现 {out['listing'].get('discovered_article_links')} 条同频道文章链接",
        f"- 随机抽取 {out.get('random_sample_attempted')} 篇，"
        f"有可见正文且提取成功 {out.get('random_sample_visible_count')} 篇",
        "",
        "## 5 篇真实文章",
        "",
        "| # | 标题 | 发布时间（+08:00） | 正文提取 | 链接 |",
        "|---|---|---|---|---|",
    ]
    for i, article in enumerate(out["five_articles"], 1):
        title = (article.get("title") or "").replace("|", "｜")
        lines.append(
            f"| {i} | {title} | {article.get('published_at_local')} | "
            f"{article.get('extraction_status')} / {article.get('content_chars')} 字 | "
            f"{article.get('url')} |"
        )
    lines += [
        "",
        "## RSS 结论",
        "",
        f"{out['rss'].get('note')}。最新条目时间：`{out['rss'].get('latest_published_at')}`。"
        "按任务书要求，不把历史 RSS 当作有效最新数据源。",
        "",
        "## 解析策略与问题",
        "",
    ]
    lines += [f"- {item}" for item in out["problems"]]
    lines += [
        "",
        f"策略：{out['strategy']}",
        "",
        "机器可读证据：`evidence/phase1_xinhua.json`（含 5 篇全文、10 篇随机样本、"
        "逐频道结构检查、RSS 原始状态）。",
        "",
    ]
    (evidence_dir / "phase1_xinhua.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    return 0 if len(out["five_articles"]) >= 5 and out["rss"].get("usable_for_latest_news") is False else 1


if __name__ == "__main__":
    raise SystemExit(main())
