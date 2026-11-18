"""Article HTML parser and safe text cleaning.

Only content inside the configured article container is treated as body text.
Navigation, share widgets, ads and related-reading blocks are removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from .config import ArticleRules, SiteConfig
from .models import ParsedArticle
from .timeutil import DateParseError, parse_article_time

_BOILERPLATE = re.compile(
    r"^(责任编辑|编辑|责编|分享到|扫一扫|返回顶部|相关阅读|相关新闻|广告|"
    r"原标题|本文来源|版权声明|免责声明)\s*[:：]?"
)
_TRAILING_BOILERPLATE = re.compile(
    r"(责任编辑\s*[:：].*|编辑\s*[:：]\s*\S{1,20})$"
)
_SPACE = re.compile(r"[\t\r\f\v\u00a0\u3000]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")


@dataclass(slots=True)
class ContentResult:
    text: str
    selector: str | None
    char_count: int
    paragraph_count: int


def clean_line(value: str) -> str:
    text = _ZERO_WIDTH.sub("", value)
    text = _SPACE.sub(" ", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def clean_content(value: str) -> str:
    value = _ZERO_WIDTH.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _SPACE.sub(" ", value)
    lines = []
    for line in value.split("\n"):
        line = clean_line(line)
        if not line:
            continue
        if _BOILERPLATE.match(line):
            continue
        if _TRAILING_BOILERPLATE.search(line) and len(line) < 80:
            continue
        if line in {"分享", "打印", "关闭", "收藏"}:
            continue
        if not lines or lines[-1] != line:
            lines.append(line)
    return _MULTI_NEWLINE.sub("\n\n", "\n".join(lines)).strip()


def _first_text(soup: BeautifulSoup, selectors: Iterable[str]) -> str | None:
    for selector in selectors:
        try:
            node = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - bad user selector is reported by validation
            continue
        if node:
            text = clean_line(node.get_text(" ", strip=True))
            if text:
                return text
    return None


def _meta_content(soup: BeautifulSoup, names: Iterable[str]) -> str | None:
    for name in names:
        node = soup.find("meta", attrs={"name": name}) or soup.find(
            "meta", attrs={"property": name}
        )
        if node and node.get("content"):
            value = clean_line(str(node.get("content")))
            if value:
                return value
    return None


class ArticleParser:
    def __init__(self, site: SiteConfig, rules: ArticleRules):
        self.site = site
        self.rules = rules
        self._datetime_re = re.compile(rules.datetime_regex)

    def parse(
        self,
        html: str,
        url: str,
        *,
        channel_id: str,
        fetched_at: str,
        http_status: int = 200,
        final_url: str | None = None,
    ) -> ParsedArticle:
        soup = BeautifulSoup(html, self.site.html_parser)
        title = self._extract_title(soup)
        raw_date, date_source = self._extract_date(soup)
        source = self._extract_source(soup)
        author = _first_text(soup, self.rules.author_selectors)
        summary = _meta_content(soup, self.rules.summary_meta_names)

        content_result = self._extract_content(soup)
        content = content_result.text if content_result else ""
        content_chars = len(re.sub(r"\s+", "", content))

        published_at = published_at_local = published_date = None
        date_precision = None
        date_error = None
        if raw_date:
            try:
                info = parse_article_time(raw_date, self.site.timezone)
                published_at = info.published_at
                published_at_local = info.published_at_local
                published_date = info.published_date
                date_precision = info.precision
            except DateParseError as exc:
                date_error = str(exc)

        errors: list[str] = []
        status = "ok"
        if not title:
            status = "failed"
            errors.append("标题提取失败")
        if not raw_date:
            status = "partial" if status != "failed" else status
            errors.append("发布时间提取失败")
        elif date_error:
            status = "partial" if status != "failed" else status
            errors.append(f"发布时间解析失败: {date_error}")
        if not content:
            status = "partial" if status != "failed" else status
            errors.append("正文提取为空")
        elif content_chars < self.rules.min_content_chars:
            status = "partial" if status != "failed" else status
            errors.append(
                f"正文过短（{content_chars} 字符，阈值 {self.rules.min_content_chars}）"
            )

        return ParsedArticle(
            title=title,
            url=url,
            published_at=published_at,
            published_at_local=published_at_local,
            published_date=published_date,
            date_precision=date_precision,
            timezone=self.site.timezone,
            content=content or None,
            summary=summary,
            author=author,
            source=source,
            fetched_at=fetched_at,
            extraction_status=status,
            extraction_error="；".join(errors) if errors else None,
            http_status=http_status,
            final_url=final_url or url,
            raw_date=raw_date,
        )

    def _extract_title(self, soup: BeautifulSoup) -> str | None:
        title = _first_text(soup, self.rules.title_selectors)
        if not title:
            if soup.title and soup.title.get_text(strip=True):
                title = clean_line(soup.title.get_text(" ", strip=True))
        if not title:
            return None
        for suffix in self.rules.strip_title_suffixes:
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        return title or None

    def _extract_date(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        meta_raw = _meta_content(soup, self.rules.date_meta_names)
        candidates: list[tuple[str, str]] = []
        if meta_raw:
            candidates.append((meta_raw, "meta"))
        for selector in self.rules.published_at_selectors:
            try:
                nodes = soup.select(selector)
            except Exception:  # noqa: BLE001
                continue
            for node in nodes:
                text = clean_line(node.get_text(" ", strip=True))
                if not text:
                    continue
                match = self._datetime_re.search(text)
                if match:
                    candidates.append((match.group(1).strip(), selector))
        if not candidates:
            return None, None
        # Prefer a full timestamp over a date-only meta value.
        def score(item: tuple[str, str]) -> tuple[int, int]:
            text = item[0]
            has_time = bool(re.search(r"\d{1,2}:\d{2}", text))
            return (1 if has_time else 0, len(text))

        return max(candidates, key=score)

    def _extract_source(self, soup: BeautifulSoup) -> str | None:
        text = _first_text(soup, self.rules.source_selectors)
        if not text:
            text = _meta_content(soup, ["source", "og:site_name"])
        if not text:
            return None
        text = re.sub(r"^\s*来源\s*[:：]\s*", "", text).strip()
        return text or None

    def _extract_content(self, soup: BeautifulSoup) -> ContentResult | None:
        candidates: list[tuple[int, str, Tag]] = []
        selectors = list(self.rules.content_selectors)
        for selector in selectors:
            try:
                nodes = soup.select(selector)
            except Exception:  # noqa: BLE001
                continue
            for node in nodes:
                text = node.get_text(" ", strip=True)
                candidates.append((len(text), selector, node))
        if not candidates and self.rules.allow_generic_content_fallback:
            for selector in self.rules.generic_content_selectors:
                try:
                    nodes = soup.select(selector)
                except Exception:  # noqa: BLE001
                    continue
                for node in nodes:
                    text = node.get_text(" ", strip=True)
                    candidates.append((len(text), f"fallback:{selector}", node))
        if not candidates:
            return None
        _, selector, node = max(candidates, key=lambda item: item[0])
        cloned = BeautifulSoup(str(node), "lxml")
        root = cloned.body or cloned
        for remove_selector in self.rules.remove_selectors:
            try:
                for bad in root.select(remove_selector):
                    bad.decompose()
            except Exception:  # noqa: BLE001
                continue
        paragraphs = []
        try:
            p_nodes = root.select(self.rules.paragraph_selector)
        except Exception:  # noqa: BLE001
            p_nodes = []
        for p in p_nodes:
            text = clean_line(p.get_text(" ", strip=True))
            if text:
                paragraphs.append(text)
        if not paragraphs:
            raw = root.get_text("\n", strip=True)
            paragraphs = [clean_line(x) for x in raw.splitlines() if clean_line(x)]
        text = clean_content("\n".join(paragraphs))
        if not text:
            return None
        return ContentResult(
            text=text,
            selector=selector,
            char_count=len(re.sub(r"\s+", "", text)),
            paragraph_count=len(paragraphs),
        )
