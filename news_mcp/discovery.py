"""RSS, sitemap and HTML article discovery sources."""

from __future__ import annotations

import json
import logging
import posixpath
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup
from lxml import etree

from .config import ChannelConfig, DiscoveryConfig, SiteConfig
from .fetch import HttpClient
from .models import Candidate, DiscoveryResult, FetchResult
from .timeutil import DateParseError, parse_article_time, to_utc_iso
from .urls import UrlValidationError, host_allowed, normalize_url

log = logging.getLogger(__name__)
UTC = timezone.utc


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(code: str, message: str, **extra) -> dict[str, str]:
    return {"code": code, "message": message, **{k: str(v) for k, v in extra.items()}}


def _url_matches(pattern: str, normalized: str) -> bool:
    """Accept either a full-URL pattern or a path-only pattern."""
    if re.fullmatch(pattern, normalized):
        return True
    return bool(re.fullmatch(pattern, urlsplit(normalized).path))


def _within_age(published_at: str | None, max_age_days: int | None, now: datetime) -> bool:
    if not max_age_days or not published_at:
        return True
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    except ValueError:
        return True
    return dt >= now - timedelta(days=max_age_days)


def _date_from_link(link: str) -> str | None:
    """Infer a date from a URL path like /2022-12/14/ when the feed omits pubDate."""
    match = re.search(r"/(20\d{2})-(\d{2})/(\d{2})/", link or "")
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=UTC
        ).isoformat(timespec="seconds")
    except ValueError:
        return None


class HtmlDiscovery:
    mode = "html"

    def __init__(self, site: SiteConfig, channel: ChannelConfig, config: DiscoveryConfig):
        self.site = site
        self.channel = channel
        self.config = config

    def discover(self, http: HttpClient, limit: int) -> DiscoveryResult:
        url = self.config.url or self.channel.url
        result = http.get(
            url,
            allowed_domains=self.site.allowed_domains,
            check_robots=self.site.respect_robots,
        )
        if not result.ok:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error(result.error_code or "FETCH_FAILED", result.error or "栏目页抓取失败")],
                diagnostics={"url": url, "http_status": result.status_code},
            )
        pattern = self.config.url_pattern or self.site.article_url_patterns[0]
        soup = BeautifulSoup(result.text or "", "lxml")
        anchors = soup.select(self.config.link_selector)
        candidates: list[Candidate] = []
        seen: set[str] = set()
        matched = 0
        channel_path = urlsplit(self.channel.url).path.rstrip("/")
        if channel_path.lower().endswith((".htm", ".html", ".shtml")):
            channel_path = posixpath.dirname(channel_path).rstrip("/")
        for anchor in anchors:
            href = anchor.get("href")
            if not href or str(href).lower().startswith(("javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(result.final_url or url, str(href))
            try:
                normalized = normalize_url(
                    absolute, host_aliases=self.site.host_aliases
                )
            except UrlValidationError:
                continue
            parts = urlsplit(normalized)
            if not host_allowed(parts.hostname or "", self.site.allowed_domains):
                continue
            if self.config.same_channel_path and channel_path and channel_path != "/":
                if not (
                    parts.path == channel_path
                    or parts.path.startswith(channel_path + "/")
                ):
                    continue
            if not _url_matches(pattern, normalized):
                continue
            matched += 1
            if normalized in seen:
                continue
            seen.add(normalized)
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if not title:
                img = anchor.find("img")
                title = (img.get("alt") or img.get("title") or "") if img else ""
            candidates.append(
                Candidate(
                    url=normalized,
                    title=title.strip(),
                    channel_id=self.channel.id,
                    source_mode=self.mode,
                )
            )
            if len(candidates) >= min(limit, self.config.max_items):
                break
        if not candidates:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[
                    _error(
                        "NO_ARTICLES_FOUND",
                        "栏目页中未找到符合文章规则的链接，页面结构可能变化",
                    )
                ],
                diagnostics={
                    "url": url,
                    "anchors": len(anchors),
                    "matched_article_links": matched,
                    "pattern": pattern,
                },
            )
        return DiscoveryResult(
            candidates,
            self.mode,
            diagnostics={
                "url": url,
                "anchors": len(anchors),
                "matched_article_links": matched,
                "returned": len(candidates),
                "pattern": pattern,
            },
        )


class RssDiscovery:
    mode = "rss"

    def __init__(self, site: SiteConfig, channel: ChannelConfig, config: DiscoveryConfig):
        self.site = site
        self.channel = channel
        self.config = config

    def discover(self, http: HttpClient, limit: int) -> DiscoveryResult:
        url = self.config.url or self.channel.url
        result = http.get(
            url,
            allowed_domains=self.site.allowed_domains,
            check_robots=self.site.respect_robots,
        )
        if not result.ok:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error(result.error_code or "RSS_FETCH_FAILED", result.error or "RSS 抓取失败")],
                diagnostics={"url": url, "http_status": result.status_code},
            )
        feed = feedparser.parse(result.text or "")
        entries = list(feed.entries or [])
        if not entries:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error("RSS_EMPTY", "RSS 可访问但没有条目")],
                diagnostics={"url": url, "bozo": bool(feed.bozo)},
            )
        now = datetime.now(UTC)
        pattern = self.config.url_pattern or self.site.article_url_patterns[0]
        candidates: list[Candidate] = []
        seen: set[str] = set()
        skipped_old = 0
        latest: str | None = None
        for entry in entries:
            link = entry.get("link")
            if not link:
                continue
            try:
                normalized = normalize_url(
                    urljoin(url, str(link)), host_aliases=self.site.host_aliases
                )
            except UrlValidationError:
                continue
            parts = urlsplit(normalized)
            if not host_allowed(parts.hostname or "", self.site.allowed_domains):
                continue
            published = None
            for key in ("published_parsed", "updated_parsed", "created_parsed"):
                value = entry.get(key)
                if value:
                    try:
                        published = to_utc_iso(datetime(*value[:6], tzinfo=UTC))
                        break
                    except (TypeError, ValueError):
                        pass
            if not published:
                published = _date_from_link(str(link))
            if published:
                if latest is None or published > latest:
                    latest = published
            if not _url_matches(pattern, normalized):
                continue
            if not _within_age(published, self.config.max_age_days, now):
                skipped_old += 1
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                Candidate(
                    url=normalized,
                    title=(entry.get("title") or "").strip(),
                    channel_id=self.channel.id,
                    source_mode=self.mode,
                    published_at=published,
                    published_at_raw=entry.get("published") or entry.get("updated"),
                )
            )
            if len(candidates) >= min(limit, self.config.max_items):
                break
        if not candidates:
            latest_old = bool(
                latest
                and self.config.max_age_days
                and not _within_age(latest, self.config.max_age_days, now)
            )
            code = "RSS_STALE" if (skipped_old or latest_old) else "RSS_NO_ARTICLE_LINKS"
            message = (
                f"RSS 最新条目为 {latest}，超过 {self.config.max_age_days} 天，"
                "不能作为最新新闻来源"
                if latest_old
                else (
                    "RSS 条目均超过新鲜度阈值"
                    if skipped_old
                    else "RSS 中没有符合文章规则的链接"
                )
            )
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error(code, message, latest=latest or "")],
                diagnostics={
                    "url": url,
                    "entries": len(entries),
                    "latest": latest,
                    "skipped_old": skipped_old,
                },
            )
        return DiscoveryResult(
            candidates,
            self.mode,
            diagnostics={
                "url": url,
                "entries": len(entries),
                "latest": latest,
                "returned": len(candidates),
            },
        )


class JsonDiscovery:
    """Discover a list from a static JSON endpoint.

    Official portals frequently expose a JSON list while the rendered channel
    page is JavaScript-only. The mapping is explicit in YAML, so this remains
    configuration-driven rather than a site-specific code branch.
    """

    mode = "json"

    def __init__(self, site: SiteConfig, channel: ChannelConfig, config: DiscoveryConfig):
        self.site = site
        self.channel = channel
        self.config = config

    def _items(self, data):
        if self.config.items_path:
            current = data
            for part in self.config.items_path.split("."):
                current = current[part]
            data = current
        if not isinstance(data, list):
            raise ValueError("items_path 未指向 JSON 数组")
        return data

    def discover(self, http: HttpClient, limit: int) -> DiscoveryResult:
        url = self.config.url or self.channel.url
        result = http.get(
            url,
            allowed_domains=self.site.allowed_domains,
            check_robots=self.site.respect_robots,
            allow_content_types={"application/json", "text/json"},
        )
        if not result.ok:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error(result.error_code or "JSON_FETCH_FAILED", result.error or "JSON 抓取失败")],
                diagnostics={"url": url, "http_status": result.status_code},
            )
        try:
            payload = json.loads(result.text or "")
            items = self._items(payload)
        except Exception as exc:  # noqa: BLE001
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error("JSON_INVALID", f"JSON 解析/路径失败: {exc}")],
                diagnostics={"url": url},
            )
        now = datetime.now(UTC)
        pattern = self.config.url_pattern or self.site.article_url_patterns[0]
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_url = item.get(self.config.url_key)
            title = str(item.get(self.config.title_key) or "").strip()
            if not raw_url:
                continue
            try:
                normalized = normalize_url(
                    str(raw_url), base=url, host_aliases=self.site.host_aliases
                )
            except UrlValidationError:
                continue
            parts = urlsplit(normalized)
            if not host_allowed(parts.hostname or "", self.site.allowed_domains):
                continue
            if not _url_matches(pattern, normalized):
                continue
            raw_date = item.get(self.config.date_key) if self.config.date_key else None
            published = None
            if raw_date:
                try:
                    published = parse_article_time(str(raw_date), self.site.timezone).published_at
                except DateParseError:
                    published = None
            if not _within_age(published, self.config.max_age_days, now):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                Candidate(
                    url=normalized,
                    title=title,
                    channel_id=self.channel.id,
                    source_mode=self.mode,
                    published_at=published,
                    published_at_raw=str(raw_date) if raw_date else None,
                )
            )
            if len(candidates) >= min(limit, self.config.max_items):
                break
        if not candidates:
            return DiscoveryResult(
                [],
                self.mode,
                errors=[_error("JSON_NO_ARTICLES", "JSON 列表中没有符合文章规则的条目")],
                diagnostics={"url": url, "items": len(items)},
            )
        return DiscoveryResult(
            candidates,
            self.mode,
            diagnostics={"url": url, "items": len(items), "returned": len(candidates)},
        )


class SitemapDiscovery:
    mode = "sitemap"

    def __init__(self, site: SiteConfig, channel: ChannelConfig, config: DiscoveryConfig):
        self.site = site
        self.channel = channel
        self.config = config

    def discover(self, http: HttpClient, limit: int) -> DiscoveryResult:
        roots = [self.config.url] if self.config.url else []
        if not roots:
            parts = urlsplit(self.channel.url)
            roots = [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
        now = datetime.now(UTC)
        pattern = self.config.url_pattern or self.site.article_url_patterns[0]
        candidates: list[Candidate] = []
        seen: set[str] = set()
        diagnostics: dict[str, object] = {"urls": roots, "sitemaps": []}
        errors: list[dict[str, str]] = []
        queue = [(root, 0) for root in roots]
        visited: set[str] = set()
        while queue and len(candidates) < min(limit, self.config.max_items):
            sm_url, depth = queue.pop(0)
            if sm_url in visited or depth > 1:
                continue
            visited.add(sm_url)
            result = http.get(
                sm_url,
                allowed_domains=self.site.allowed_domains,
                check_robots=self.site.respect_robots,
            )
            if not result.ok:
                errors.append(
                    _error(
                        result.error_code or "SITEMAP_FETCH_FAILED",
                        result.error or "Sitemap 抓取失败",
                        url=sm_url,
                    )
                )
                continue
            try:
                root = etree.fromstring(
                    (result.text or "").encode("utf-8"),
                    parser=etree.XMLParser(resolve_entities=False, no_network=True),
                )
            except etree.XMLSyntaxError as exc:
                errors.append(_error("SITEMAP_INVALID_XML", str(exc), url=sm_url))
                continue
            tag = etree.QName(root).localname.lower()
            cast = list(diagnostics["sitemaps"])
            cast.append({"url": sm_url, "type": tag})
            diagnostics["sitemaps"] = cast
            if tag == "sitemapindex":
                for loc in root.xpath("//*[local-name()='sitemap']/*[local-name()='loc']/text()"):
                    child = str(loc).strip()
                    if child and child not in visited:
                        queue.append((child, depth + 1))
                continue
            for node in root.xpath("//*[local-name()='url']"):
                loc = node.find("{*}loc")
                if loc is None or not loc.text:
                    continue
                try:
                    normalized = normalize_url(
                        loc.text.strip(), host_aliases=self.site.host_aliases
                    )
                except UrlValidationError:
                    continue
                parts = urlsplit(normalized)
                if not host_allowed(parts.hostname or "", self.site.allowed_domains):
                    continue
                if not _url_matches(pattern, normalized):
                    continue
                lastmod = node.find("{*}lastmod")
                published = None
                if lastmod is not None and lastmod.text:
                    try:
                        info = parse_article_time(lastmod.text.strip(), self.site.timezone)
                        published = info.published_at
                    except DateParseError:
                        published = None
                if not _within_age(published, self.config.max_age_days, now):
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                candidates.append(
                    Candidate(
                        url=normalized,
                        title="",
                        channel_id=self.channel.id,
                        source_mode=self.mode,
                        published_at=published,
                        published_at_raw=lastmod.text.strip() if lastmod is not None and lastmod.text else None,
                    )
                )
                if len(candidates) >= min(limit, self.config.max_items):
                    break
        if not candidates:
            errors.append(_error("SITEMAP_NO_ARTICLES", "Sitemap 中未找到符合条件的文章"))
            return DiscoveryResult([], self.mode, errors=errors, diagnostics=diagnostics)
        return DiscoveryResult(candidates, self.mode, errors=errors, diagnostics=diagnostics)


def build_discovery(
    site: SiteConfig, channel: ChannelConfig, config: DiscoveryConfig
):
    if config.type == "html":
        return HtmlDiscovery(site, channel, config)
    if config.type == "rss":
        return RssDiscovery(site, channel, config)
    if config.type == "sitemap":
        return SitemapDiscovery(site, channel, config)
    if config.type == "json":
        return JsonDiscovery(site, channel, config)
    raise DiscoveryError("UNKNOWN_DISCOVERY_TYPE", f"未知发现方式: {config.type}")


def fetch_result_summary(result: FetchResult) -> dict[str, object]:
    return {
        "url": result.url,
        "final_url": result.final_url,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "elapsed_ms": result.elapsed_ms,
        "error_code": result.error_code,
        "error": result.error,
    }
