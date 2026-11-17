"""Advisory website inspection: infer candidate feeds, links and selectors.

Nothing produced here is marked valid. The output is explicitly a candidate
configuration that must be copied into config/sites/*.yaml and then checked with
validate_site, which fetches real samples.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
import yaml
from bs4 import BeautifulSoup

from .config import ArticleRules, ChannelConfig, DiscoveryConfig, SiteConfig
from .fetch import HttpClient
from .parsers import ArticleParser
from .timeutil import DateParseError, parse_article_time
from .urls import UrlValidationError, normalize_url, resolve_public_host

UTC = timezone.utc
_TITLE_SELECTORS = ["h1 .title", "h1", ".article-title", ".article_title", ".title"]
_DATE_SELECTORS = [
    "meta[name=publishdate]",
    "meta[name=pubdate]",
    "meta[property='article:published_time']",
    "time",
    ".time",
    ".date",
    ".info",
    ".content_left_time",
    ".article-time",
    ".pubtime",
    ".publish-time",
    "#pubtime",
]
_CONTENT_SELECTORS = [
    "#detailContent",
    "article",
    "[itemprop=articleBody]",
    ".article-content",
    ".article_content",
    ".article-body",
    ".post-content",
    ".left_zw",
    ".content",
    "main",
]
_BAD_PATH = re.compile(
    r"(login|signin|register|about|contact|privacy|terms|search|tag/|tags/|"
    r"category/|author/|rss|feed|sitemap|javascript|mailto|"
    r"/(?:shipin|video|photo|pic|tupian)/)",
    re.I,
)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


class SiteInspector:
    def inspect(self, url: str) -> dict[str, Any]:
        started = _iso_now()
        try:
            normalized = normalize_url(url)
            parts = urlsplit(normalized)
            if parts.scheme not in {"http", "https"}:
                raise UrlValidationError("UNSUPPORTED_SCHEME", "仅支持 http/https URL")
            resolve_public_host(parts.hostname or "", parts.port or 443)
        except UrlValidationError as exc:
            return {
                "ok": False,
                "status": "failed",
                "validated": False,
                "error_code": exc.code,
                "error": exc.message,
                "inspected_at": started,
            }

        host = parts.hostname or ""
        allowed = [host]
        if host.startswith("www."):
            allowed.append(host[4:])
        site = SiteConfig(
            site_id="candidate",
            name=host,
            homepage=f"{parts.scheme}://{parts.netloc}/",
            timezone="Asia/Shanghai",
            allowed_domains=allowed,
            article_url_patterns=[r"^https?://[^/]+/.*$"],
            request_delay_seconds=1.0,
            respect_robots=True,
            channels=[
                ChannelConfig(
                    id="candidate",
                    name="候选频道",
                    url=normalized,
                    discovery=[DiscoveryConfig(type="html", url=normalized)],
                )
            ],
            article=ArticleRules(),
        )
        http = HttpClient(site, default_user_agent="news-mcp-inspector/1.0")
        warnings: list[str] = []
        checks: list[dict[str, Any]] = []
        try:
            page = http.get(
                normalized,
                allowed_domains=allowed,
                check_robots=True,
            )
            if not page.ok:
                return {
                    "ok": False,
                    "status": "failed",
                    "validated": False,
                    "url": normalized,
                    "error_code": page.error_code or "FETCH_FAILED",
                    "error": page.error or "页面抓取失败",
                    "warnings": ["未抓取到页面，无法生成候选配置。"],
                    "inspected_at": started,
                }
            html = page.text or ""
            soup = BeautifulSoup(html, "lxml")
            checks.append(
                {
                    "name": "fetch",
                    "ok": True,
                    "detail": f"HTTP {page.status_code}, {len(html)} 字符",
                    "evidence": {
                        "final_url": page.final_url,
                        "content_type": page.content_type,
                        "elapsed_ms": page.elapsed_ms,
                    },
                }
            )
            page_title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
            site_name = re.sub(r"[-_|].*$", "", page_title).strip() or host
            robots_text, robots_checks = self._inspect_robots(http, site, parts)
            checks.extend(robots_checks)
            rss = self._inspect_rss(http, site, soup, parts, normalized)
            if rss.get("candidates"):
                checks.append(
                    {
                        "name": "rss",
                        "ok": True,
                        "detail": f"发现 {len(rss['candidates'])} 个 RSS/Atom 候选",
                        "evidence": rss,
                    }
                )
            else:
                warnings.append("未发现可访问的 RSS/Atom 订阅源。")
            sitemap = self._inspect_sitemap(http, site, parts, robots_text)
            if sitemap.get("candidates"):
                checks.append(
                    {
                        "name": "sitemap",
                        "ok": True,
                        "detail": f"发现 {len(sitemap['candidates'])} 个 Sitemap 候选",
                        "evidence": sitemap,
                    }
                )
            links = self._article_links(soup, page.final_url or normalized, allowed)
            if not links:
                return {
                    "ok": False,
                    "status": "failed",
                    "validated": False,
                    "url": normalized,
                    "site_name": site_name,
                    "checks": checks,
                    "rss": rss,
                    "sitemap": sitemap,
                    "samples": [],
                    "warnings": warnings + ["页面中没有识别出文章链接候选。"],
                    "inspected_at": started,
                }
            checks.append(
                {
                    "name": "article_links",
                    "ok": True,
                    "detail": f"识别出 {len(links)} 个文章链接候选",
                    "evidence": links[:10],
                }
            )
            sample_results = []
            for link in links[:5]:
                sample = self._sample_article(http, site, link, allowed)
                sample_results.append(sample)
            good = [
                s
                for s in sample_results
                if s.get("title") and s.get("content_chars", 0) >= 80
            ]
            title_selectors, date_selectors, content_selectors = self._choose_selectors(
                sample_results
            )
            candidate_ready = len(good) >= 2
            if not candidate_ready:
                warnings.append(
                    "真实样本不足 2 篇同时具备标题和正文，候选配置未达到可用门槛；"
                    "不得将其视为已验证配置。"
                )
            if rss.get("latest") and rss.get("usable_recent") is False:
                warnings.append(
                    f"RSS 最新条目为 {rss.get('latest')}，超过 30 天，不应作为最新新闻来源。"
                )
            if sitemap.get("candidates"):
                warnings.append("Sitemap 仅作为候选发现方式，仍需验证文章正文提取。")
            domain_pattern = self._general_pattern(host, links)
            yaml_text = self._candidate_yaml(
                site_id=self._site_id(host),
                name=site_name,
                homepage=site.homepage,
                allowed=allowed,
                channel_url=normalized,
                channel_name=site_name,
                pattern=domain_pattern,
                title_selectors=title_selectors,
                date_selectors=date_selectors,
                content_selectors=content_selectors,
            )
            status = "candidate" if candidate_ready else "needs_manual_review"
            return {
                "ok": True,
                "status": status,
                "validated": False,
                "candidate_ready": candidate_ready,
                "url": normalized,
                "final_url": page.final_url,
                "site_name": site_name,
                "page_title": page_title,
                "checks": checks,
                "rss": rss,
                "sitemap": sitemap,
                "links": links[:10],
                "samples": sample_results,
                "suggested_selectors": {
                    "title": title_selectors,
                    "published_at": date_selectors,
                    "content": content_selectors,
                },
                "candidate_config_yaml": yaml_text,
                "warnings": warnings,
                "next_step": (
                    "将 candidate_config_yaml 保存为 config/sites/<site_id>.yaml，"
                    "然后运行 python -m news_mcp.cli validate --site <site_id>；"
                    "validate_site 通过后才会出现在 list_channels 中。"
                ),
                "inspected_at": started,
            }
        except Exception as exc:  # noqa: BLE001 - inspection must not crash MCP
            return {
                "ok": False,
                "status": "failed",
                "validated": False,
                "url": normalized,
                "error_code": "INSPECT_EXCEPTION",
                "error": f"{type(exc).__name__}: {exc}",
                "inspected_at": started,
            }
        finally:
            http.close()

    # ------------------------------------------------------------------ robots
    def _inspect_robots(
        self, http: HttpClient, site: SiteConfig, parts
    ) -> tuple[str, list[dict[str, Any]]]:
        origin = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        robots_url = origin + "robots.txt"
        result = http.get(
            robots_url,
            allowed_domains=site.allowed_domains,
            check_robots=False,
        )
        if not result.ok:
            return "", [
                {
                    "name": "robots",
                    "ok": False,
                    "detail": result.error or "robots.txt 不可访问",
                    "evidence": {"url": robots_url, "http_status": result.status_code},
                }
            ]
        text = result.text or ""
        disallows = [
            line.strip()
            for line in text.splitlines()
            if line.strip().lower().startswith("disallow:")
            and line.split(":", 1)[1].strip() not in {"", "/"}
        ]
        return text, [
            {
                "name": "robots",
                "ok": True,
                "detail": "robots.txt 可访问",
                "evidence": {
                    "url": robots_url,
                    "http_status": result.status_code,
                    "disallow_rules": disallows[:20],
                    "preview": text[:1000],
                },
            }
        ]

    # --------------------------------------------------------------------- rss
    def _inspect_rss(
        self, http: HttpClient, site: SiteConfig, soup: BeautifulSoup, parts, page_url: str
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in soup.find_all("link", href=True):
            rel = " ".join(link.get("rel") or []).lower()
            typ = (link.get("type") or "").lower()
            if "alternate" in rel and ("rss" in typ or "atom" in typ or "xml" in typ):
                candidate = urljoin(page_url, link["href"])
                if candidate not in seen:
                    seen.add(candidate)
                    candidates.append({"url": candidate, "source": "autodiscovery"})
        if not candidates:
            for suffix in ("/rss.xml", "/feed", "/index.xml"):
                candidate = f"{parts.scheme}://{parts.netloc}{suffix}"
                candidates.append({"url": candidate, "source": "common_path"})
        tested = []
        latest = None
        for item in candidates[:3]:
            try:
                result = http.get(
                    item["url"],
                    allowed_domains=site.allowed_domains,
                    check_robots=site.respect_robots,
                )
            except Exception as exc:  # noqa: BLE001
                tested.append({"url": item["url"], "source": item["source"], "error": str(exc)})
                continue
            if not result.ok:
                tested.append(
                    {
                        "url": item["url"],
                        "source": item["source"],
                        "http_status": result.status_code,
                        "error": result.error,
                    }
                )
                continue
            feed = feedparser.parse(result.text or "")
            entries = list(feed.entries or [])
            feed_latest = None
            for entry in entries[:20]:
                for key in ("published_parsed", "updated_parsed"):
                    value = entry.get(key)
                    if value:
                        dt = datetime(*value[:6], tzinfo=UTC)
                        iso = dt.isoformat(timespec="seconds")
                        if feed_latest is None or iso > feed_latest:
                            feed_latest = iso
                        break
            if feed_latest and (latest is None or feed_latest > latest):
                latest = feed_latest
            tested.append(
                {
                    "url": item["url"],
                    "source": item["source"],
                    "http_status": result.status_code,
                    "entries": len(entries),
                    "latest": feed_latest,
                    "title": feed.feed.get("title"),
                }
            )
            if entries:
                break
        usable_recent = None
        if latest:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(latest)).days
                usable_recent = age <= 30
            except ValueError:
                usable_recent = None
        successful = [item for item in tested if item.get("entries")]
        return {
            "candidates": successful,
            "tested": tested,
            "latest": latest,
            "usable_recent": usable_recent,
        }

    # ----------------------------------------------------------------- sitemap
    def _inspect_sitemap(
        self, http: HttpClient, site: SiteConfig, parts, robots_text: str
    ) -> dict[str, Any]:
        candidates = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots_text or "")
        if not candidates:
            candidates = [f"{parts.scheme}://{parts.netloc}/sitemap.xml"]
        tested = []
        for candidate in candidates[:2]:
            result = http.get(
                candidate,
                allowed_domains=site.allowed_domains,
                check_robots=site.respect_robots,
            )
            if not result.ok:
                tested.append(
                    {
                        "url": candidate,
                        "http_status": result.status_code,
                        "error": result.error,
                    }
                )
                continue
            locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", result.text or "", re.I)
            tested.append(
                {
                    "url": candidate,
                    "http_status": result.status_code,
                    "locations": len(locs),
                    "sample": [x.strip() for x in locs[:5]],
                }
            )
        successful = [x for x in tested if x.get("locations")]
        return {"candidates": successful, "tested": tested}

    # ------------------------------------------------------------------- links
    def _article_links(
        self, soup: BeautifulSoup, base: str, allowed_domains: list[str]
    ) -> list[str]:
        scored: list[tuple[int, str]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base, href)
            try:
                normalized = normalize_url(absolute)
            except UrlValidationError:
                continue
            parts = urlsplit(normalized)
            if not any(
                parts.hostname == d or (parts.hostname or "").endswith("." + d)
                for d in allowed_domains
            ):
                continue
            path = parts.path
            if _BAD_PATH.search(path):
                continue
            score = 0
            if re.search(r"/20\d{2}[-/]?\d{2}(?:[-/]?\d{2})?/", path):
                score += 4
            if re.search(r"/20\d{6}/", path):
                score += 2
            if re.search(r"\d{6,}", path):
                score += 2
            if re.search(r"\.(?:s?html?|php|aspx)$", path, re.I):
                score += 2
            if re.search(r"/(?:c|content|article|detail|news)[^/]*\.", path, re.I):
                score += 1
            text = _clean(anchor.get_text(" ", strip=True))
            if len(text) >= 8:
                score += 1
            if score < 4:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            scored.append((score, normalized))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [url for _, url in scored[:20]]

    # ----------------------------------------------------------------- samples
    def _sample_article(
        self, http: HttpClient, site: SiteConfig, url: str, allowed: list[str]
    ) -> dict[str, Any]:
        result = http.get(
            url,
            allowed_domains=site.allowed_domains,
            check_robots=site.respect_robots,
        )
        sample: dict[str, Any] = {
            "url": url,
            "http_status": result.status_code,
            "fetch_error": result.error,
        }
        if not result.ok:
            return sample
        soup = BeautifulSoup(result.text or "", "lxml")
        title, title_selector = self._best_title(soup)
        date_raw, date_selector = self._best_date(soup)
        content, content_selector, content_chars = self._best_content(soup)
        sample.update(
            {
                "final_url": result.final_url,
                "title": title,
                "title_selector": title_selector,
                "date_raw": date_raw,
                "date_selector": date_selector,
                "content_selector": content_selector,
                "content_preview": (content or "")[:200],
                "content_chars": content_chars,
            }
        )
        if date_raw:
            try:
                info = parse_article_time(date_raw, site.timezone)
                sample["published_at"] = info.published_at
                sample["published_at_local"] = info.published_at_local
            except DateParseError:
                sample["date_error"] = "发布时间无法解析"
        return sample

    def _best_title(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        for selector in _TITLE_SELECTORS:
            node = soup.select_one(selector)
            if node:
                text = _clean(node.get_text(" ", strip=True))
                if text:
                    return text, selector
        return None, None

    def _best_date(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        for selector in _DATE_SELECTORS:
            nodes = soup.select(selector)
            for node in nodes:
                if node.name == "meta":
                    text = _clean(node.get("content"))
                else:
                    text = _clean(node.get("datetime") or node.get_text(" ", strip=True))
                match = re.search(
                    r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?"
                    r"(?:\s*[T ]?\s*\d{1,2}:\d{2}(?::\d{2})?)?",
                    text or "",
                )
                if match:
                    return match.group(0), selector
        return None, None

    def _best_content(self, soup: BeautifulSoup) -> tuple[str | None, str | None, int]:
        specific: tuple[int, str, str] | None = None
        fallback: tuple[int, str, str] | None = None
        generic = {".content", "main"}
        for selector in _CONTENT_SELECTORS:
            try:
                nodes = soup.select(selector)
            except Exception:  # noqa: BLE001
                continue
            for node in nodes:
                text = node.get_text("\n", strip=True)
                chars = len("".join(text.split()))
                candidate = (chars, selector, text)
                if chars < 40:
                    continue
                if selector in generic:
                    if fallback is None or chars > fallback[0]:
                        fallback = candidate
                elif specific is None or chars > specific[0]:
                    specific = candidate
        best = specific or fallback
        if best is None:
            return None, None, 0
        chars, selector, text = best
        return text, selector, chars

    def _choose_selectors(
        self, samples: list[dict[str, Any]]
    ) -> tuple[list[str], list[str], list[str]]:
        def ranked(key: str) -> list[str]:
            counts: dict[str, int] = {}
            for sample in samples:
                value = sample.get(key)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            return [
                selector
                for selector, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]

        titles = ranked("title_selector") or ["h1"]
        dates = ranked("date_selector") or ["meta[name=publishdate]", ".time"]
        contents = ranked("content_selector") or ["article", ".content"]
        return titles, dates, contents

    def _general_pattern(self, host: str, links: list[str]) -> str:
        host_pattern = re.escape(host)
        if host.startswith("www."):
            host_pattern = r"(?:www\.)?" + re.escape(host[4:])
        suffixes = set()
        for link in links:
            path = urlsplit(link).path
            if path.endswith(".shtml"):
                suffixes.add(r"\.shtml")
            elif path.endswith(".html"):
                suffixes.add(r"\.html")
        suffix = "|".join(sorted(suffixes)) if suffixes else r"\.s?html?"
        return rf"^https?://{host_pattern}/.*(?:{suffix})$"

    def _site_id(self, host: str) -> str:
        base = host.removeprefix("www.")
        value = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
        if not value or not value[0].isalpha():
            value = "site_" + value
        return value[:32]

    def _candidate_yaml(
        self,
        *,
        site_id: str,
        name: str,
        homepage: str,
        allowed: list[str],
        channel_url: str,
        channel_name: str,
        pattern: str,
        title_selectors: list[str],
        date_selectors: list[str],
        content_selectors: list[str],
    ) -> str:
        data = {
            "site_id": site_id,
            "name": name,
            "homepage": homepage,
            "enabled": False,
            "timezone": "Asia/Shanghai",
            "allowed_domains": allowed,
            "article_url_patterns": [pattern],
            "request_delay_seconds": 1.5,
            "respect_robots": True,
            "channels": [
                {
                    "id": "main",
                    "name": channel_name,
                    "url": channel_url,
                    "discovery": [
                        {
                            "type": "html",
                            "url": channel_url,
                            "url_pattern": pattern,
                        }
                    ],
                }
            ],
            "article": {
                "title_selectors": title_selectors,
                "published_at_selectors": date_selectors,
                "content_selectors": content_selectors,
                "min_content_chars": 80,
            },
        }
        header = (
            "# CANDIDATE ONLY - generated by inspect_site.\n"
            "# Not validated. Review selectors, set enabled: true, then run validate_site.\n"
        )
        return header + yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
