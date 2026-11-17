"""SiteAdapter contract, config-driven generic adapter and custom adapter hook."""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import ChannelConfig, SiteConfig
from .discovery import build_discovery
from .fetch import HttpClient
from .models import Candidate, DiscoveryResult, FetchResult, ParsedArticle, ValidationCheck
from .parsers import ArticleParser
from .timeutil import now_utc
from .urls import UrlValidationError, normalize_url, validate_article_url

log = logging.getLogger(__name__)
UTC = timezone.utc


class SiteAdapter(ABC):
    """Unified interface for every data source."""

    def __init__(self, site: SiteConfig, http: HttpClient):
        self.site = site
        self.http = http

    @abstractmethod
    def discover_articles(self, channel_id: str, limit: int) -> DiscoveryResult:
        raise NotImplementedError

    @abstractmethod
    def fetch_article(self, url: str) -> FetchResult:
        raise NotImplementedError

    @abstractmethod
    def parse_article(
        self,
        fetch: FetchResult,
        *,
        channel_id: str,
        candidate: Candidate | None = None,
    ) -> ParsedArticle:
        raise NotImplementedError

    @abstractmethod
    def validate_source(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        self.http.close()


class GenericSiteAdapter(SiteAdapter):
    """Adapter composed from YAML: discovery modes + declarative HTML selectors."""

    def discover_articles(self, channel_id: str, limit: int) -> DiscoveryResult:
        channel = self.site.channel(channel_id)
        if channel is None:
            return DiscoveryResult(
                [],
                "none",
                errors=[{"code": "UNKNOWN_CHANNEL", "message": f"未知频道: {channel_id}"}],
            )
        if not channel.enabled:
            return DiscoveryResult(
                [],
                "none",
                errors=[{"code": "CHANNEL_DISABLED", "message": f"频道已禁用: {channel_id}"}],
            )
        sources = channel.discovery or []
        all_errors: list[dict[str, str]] = []
        diagnostics: dict[str, Any] = {"attempted_sources": []}
        for config in sources:
            try:
                source = build_discovery(self.site, channel, config)
                result = source.discover(self.http, limit)
            except Exception as exc:  # noqa: BLE001 - one source must not kill others
                log.exception("discovery source crashed for %s/%s", self.site.site_id, channel_id)
                all_errors.append(
                    {"code": "DISCOVERY_EXCEPTION", "message": f"{type(exc).__name__}: {exc}"}
                )
                diagnostics["attempted_sources"].append(
                    {"type": config.type, "ok": False, "error": str(exc)}
                )
                continue
            diagnostics["attempted_sources"].append(
                {
                    "type": result.source_mode,
                    "ok": bool(result.candidates),
                    "returned": len(result.candidates),
                    "diagnostics": result.diagnostics,
                }
            )
            all_errors.extend(result.errors)
            if result.candidates:
                diagnostics.update(result.diagnostics)
                return DiscoveryResult(
                    result.candidates,
                    result.source_mode,
                    errors=all_errors,
                    diagnostics=diagnostics,
                )
        return DiscoveryResult([], "none", errors=all_errors, diagnostics=diagnostics)

    def fetch_article(self, url: str) -> FetchResult:
        try:
            validate_article_url(
                url,
                allowed_domains=self.site.allowed_domains,
                patterns=self.site.article_url_patterns,
                host_aliases=self.site.host_aliases,
                check_dns=True,
            )
        except UrlValidationError as exc:
            return FetchResult(url=url, error_code=exc.code, error=exc.message)
        return self.http.get(
            url,
            allowed_domains=self.site.allowed_domains,
            patterns=self.site.article_url_patterns,
            check_robots=self.site.respect_robots,
            check_dns=True,
        )

    def parse_article(
        self,
        fetch: FetchResult,
        *,
        channel_id: str,
        candidate: Candidate | None = None,
    ) -> ParsedArticle:
        if not fetch.ok or not fetch.text:
            return ParsedArticle(
                title=None,
                url=fetch.final_url or fetch.url,
                published_at=None,
                published_at_local=None,
                published_date=None,
                date_precision=None,
                timezone=self.site.timezone,
                content=None,
                summary=None,
                author=None,
                source=None,
                fetched_at=now_utc(),
                extraction_status="failed",
                extraction_error=fetch.error or f"HTTP {fetch.status_code}",
                http_status=fetch.status_code,
                final_url=fetch.final_url,
            )
        parser = ArticleParser(self.site, self.site.rules_for(channel_id))
        return parser.parse(
            fetch.text,
            fetch.final_url or fetch.url,
            channel_id=channel_id,
            fetched_at=now_utc(),
            http_status=fetch.status_code or 200,
            final_url=fetch.final_url,
        )

    def validate_source(self) -> dict[str, Any]:
        channels_report: list[dict[str, Any]] = []
        enabled = [c for c in self.site.channels if c.enabled][: self.site.validation.max_channels]
        for channel in enabled:
            report = self._validate_channel(channel)
            channels_report.append(report)
        valid_channels = [r for r in channels_report if r["ok"]]
        return {
            "site_id": self.site.site_id,
            "validated_at": now_utc(),
            "ok": bool(valid_channels),
            "valid_channels": len(valid_channels),
            "total_channels_checked": len(channels_report),
            "channels": channels_report,
            "errors": [
                error
                for report in channels_report
                for error in report.get("errors", [])
            ],
        }

    def _validate_channel(self, channel: ChannelConfig) -> dict[str, Any]:
        checks: list[ValidationCheck] = []
        errors: list[dict[str, str]] = []
        samples: list[dict[str, Any]] = []
        discovery = self.discover_articles(
            channel.id, max(self.site.validation.sample_articles * 3, 3)
        )
        checks.append(
            ValidationCheck(
                "discovery",
                bool(discovery.candidates),
                f"发现 {len(discovery.candidates)} 篇文章链接",
                discovery.diagnostics,
            )
        )
        errors.extend(discovery.errors)
        ok_count = 0
        for candidate in discovery.candidates[: self.site.validation.sample_articles]:
            fetch = self.fetch_article(candidate.url)
            sample: dict[str, Any] = {
                "url": candidate.url,
                "http_status": fetch.status_code,
                "fetch_error": fetch.error,
            }
            if fetch.ok:
                parsed = self.parse_article(fetch, channel_id=channel.id, candidate=candidate)
                sample.update(
                    {
                        "title": parsed.title,
                        "published_at": parsed.published_at,
                        "published_at_local": parsed.published_at_local,
                        "content_chars": len((parsed.content or "").replace("\n", "")),
                        "content_preview": (parsed.content or "")[:160],
                        "extraction_status": parsed.extraction_status,
                        "extraction_error": parsed.extraction_error,
                    }
                )
                if parsed.extraction_status == "ok":
                    ok_count += 1
            else:
                sample["extraction_status"] = "failed"
                sample["extraction_error"] = fetch.error
            samples.append(sample)
        checks.append(
            ValidationCheck(
                "article_parse",
                ok_count >= self.site.validation.min_ok_articles,
                f"{ok_count}/{len(samples)} 个样本正文提取成功",
                {"samples": samples},
            )
        )
        if ok_count < self.site.validation.min_ok_articles:
            errors.append(
                {
                    "code": "CONTENT_VALIDATION_FAILED",
                    "message": f"频道 {channel.id} 没有足够可解析正文的样本",
                }
            )
        return {
            "channel_id": channel.id,
            "name": channel.name,
            "url": channel.url,
            "ok": bool(discovery.candidates) and ok_count >= self.site.validation.min_ok_articles,
            "checks": [check.as_dict() for check in checks],
            "samples": samples,
            "errors": errors,
        }


def load_custom_adapter(site: SiteConfig, http: HttpClient) -> SiteAdapter:
    if not site.custom_adapter:
        return GenericSiteAdapter(site, http)
    module_name, _, class_name = site.custom_adapter.partition(":")
    if not module_name or not class_name:
        raise ValueError("custom_adapter 必须使用 'module:ClassName' 格式")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not isinstance(cls, type) or not issubclass(cls, SiteAdapter):
        raise TypeError(f"{site.custom_adapter} 不是 SiteAdapter 子类")
    adapter = cls(site, http)
    log.info("loaded custom adapter %s for %s", site.custom_adapter, site.site_id)
    return adapter


@dataclass(slots=True)
class AdapterBundle:
    adapter: SiteAdapter
    channel_names: dict[str, str] = field(default_factory=dict)
