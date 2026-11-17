"""Application service shared by CLI and MCP tools.

The service is deliberately transport-agnostic: MCP, CLI and tests call the
same methods, so a successful MCP call proves the same collection path.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .adapters import SiteAdapter, load_custom_adapter
from .config import ConfigRegistry, SiteConfig, load_registry
from .fetch import HttpClient
from .inspect_site import SiteInspector
from .models import Candidate
from .storage import Database
from .timeutil import DateParseError, now_utc, search_bounds
from .urls import (
    UrlValidationError,
    normalize_url,
    validate_article_url,
)

log = logging.getLogger(__name__)
UTC = timezone.utc


def error_response(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "error_code": code,
        "error": message,
        **extra,
    }


def _age_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - dt).total_seconds()))
    except ValueError:
        return None


@dataclass(slots=True)
class ResolvedArticle:
    site: SiteConfig
    url: str
    channel_id: str
    channel_name: str


class NewsService:
    def __init__(
        self,
        registry: ConfigRegistry,
        db: Database,
        *,
        transport_factory=None,
        offline: bool | None = None,
    ):
        self.registry = registry
        self.db = db
        self._transport_factory = transport_factory
        self._offline = offline
        self._adapters: dict[str, SiteAdapter] = {}
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        config_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        **kwargs: Any,
    ) -> "NewsService":
        registry = load_registry(config_dir)
        if db_path is None:
            db_path = os.environ.get("NEWS_MCP_DB") or Path("data") / "news.db"
        db = Database(db_path)
        service = cls(registry, db, **kwargs)
        service.initialize()
        return service

    def initialize(self) -> None:
        self.db.seed_sites(list(self.registry.sites.values()))
        log.info(
            "initialized %d site(s), %d article(s)",
            len(self.registry.sites),
            self.db.count_articles(),
        )

    def close(self) -> None:
        with self._lock:
            for adapter in self._adapters.values():
                adapter.close()
            self._adapters.clear()

    def _adapter(self, site: SiteConfig) -> SiteAdapter:
        with self._lock:
            adapter = self._adapters.get(site.site_id)
            if adapter is not None:
                return adapter
            transport = None
            if self._transport_factory is not None:
                transport = self._transport_factory(site.site_id)
            http = HttpClient(
                site,
                default_user_agent=self.registry.settings.user_agent,
                transport=transport,
                offline=self._offline,
            )
            adapter = load_custom_adapter(site, http)
            self._adapters[site.site_id] = adapter
            return adapter

    # ------------------------------------------------------------------- sites
    def list_sites(self) -> dict[str, Any]:
        rows = {row["site_id"]: row for row in self.db.list_sites()}
        sites = []
        for site in self.registry.sites.values():
            row = rows.get(site.site_id, {})
            sites.append(
                {
                    "site_id": site.site_id,
                    "name": site.name,
                    "homepage": site.homepage,
                    "enabled": site.enabled,
                    "timezone": site.timezone,
                    "status": row.get("status", "not_registered"),
                    "validated_at": row.get("validated_at"),
                    "channels_total": row.get("channel_count", len(site.channels)),
                    "channels_valid": row.get("valid_channel_count", 0),
                    "articles": row.get("article_count", 0),
                    "last_refresh_at": row.get("last_refresh_at"),
                    "config_hash": site.config_hash,
                    "config_error": None,
                }
            )
        return {
            "ok": not self.registry.errors,
            "status": "ok" if not self.registry.errors else "partial",
            "sites": sites,
            "config_errors": self.registry.errors,
            "default_site": self.registry.settings.default_site,
        }

    def list_channels(self, site_id: str | None = None) -> dict[str, Any]:
        scope = site_id
        if scope and scope != "all" and scope not in self.registry.sites:
            return error_response("UNKNOWN_SITE", f"未知站点: {scope}")
        if scope == "all":
            scope = None
        rows = self.db.list_channels(scope, valid_only=True)
        channels = []
        for row in rows:
            site = self.registry.get(row["site_id"])
            channels.append(
                {
                    "site_id": row["site_id"],
                    "site_name": row["site_name"],
                    "channel": row["channel_id"],
                    "channel_id": row["channel_id"],
                    "name": row["name"],
                    "url": row["url"],
                    "source_url": row["url"],
                    "validated": bool(row["validated"]),
                    "validation_status": row["validation_status"],
                    "last_checked_at": row["last_checked_at"],
                    "last_refresh_at": row["last_refresh_at"],
                    "article_count": row["article_count"],
                    "last_error": row["last_error"],
                    "timezone": site.timezone if site else None,
                }
            )
        return {
            "ok": True,
            "status": "ok",
            "site_id": site_id,
            "validated_only": True,
            "channels": channels,
            "count": len(channels),
            "note": "仅返回已通过 validate_site 真实验证的频道。",
        }

    # ---------------------------------------------------------------- refresh
    def refresh_news(
        self,
        channel: str,
        limit: int = 10,
        site_id: str | None = None,
    ) -> dict[str, Any]:
        site, channel_error = self._site_for_write(site_id)
        if channel_error:
            return channel_error
        assert site is not None
        if not (1 <= int(limit) <= self.registry.settings.max_limit):
            return error_response(
                "INVALID_LIMIT",
                f"limit 必须在 1..{self.registry.settings.max_limit} 之间",
            )
        channel_row = self.db.get_channel(site.site_id, channel)
        if channel_row is None:
            return error_response(
                "UNKNOWN_CHANNEL", f"站点 {site.site_id} 没有频道 {channel}"
            )
        if not channel_row["enabled"]:
            return error_response(
                "CHANNEL_DISABLED", f"频道 {site.site_id}/{channel} 已禁用"
            )

        run_id = self.db.start_refresh_run(site.site_id, channel, int(limit))
        started = now_utc()
        errors: list[dict[str, Any]] = []
        inserted = updated = failed = partial = attempted = 0
        inserted_urls: set[str] = set()
        updated_urls: set[str] = set()
        live_urls: set[str] = set()
        discovered = 0
        source_mode = None
        adapter = self._adapter(site)
        try:
            discovery = adapter.discover_articles(channel, int(limit))
            source_mode = discovery.source_mode
            discovered = len(discovery.candidates)
            errors.extend(discovery.errors)
            if not discovery.candidates:
                status = "error"
                errors.append(
                    {
                        "code": "NO_CANDIDATES",
                        "message": "没有发现任何文章链接，未进行文章抓取",
                    }
                )
            else:
                for candidate in discovery.candidates[: int(limit)]:
                    attempted += 1
                    fetch = adapter.fetch_article(candidate.url)
                    if not fetch.ok:
                        failed += 1
                        errors.append(
                            {
                                "url": candidate.url,
                                "code": fetch.error_code or "FETCH_FAILED",
                                "message": fetch.error or "文章抓取失败",
                                "http_status": fetch.status_code,
                            }
                        )
                        continue
                    live_urls.add(normalize_url(candidate.url, host_aliases=site.host_aliases))
                    parsed = adapter.parse_article(
                        fetch, channel_id=channel, candidate=candidate
                    )
                    if parsed.extraction_status == "failed":
                        failed += 1
                        errors.append(
                            {
                                "url": candidate.url,
                                "code": "PARSE_FAILED",
                                "message": parsed.extraction_error or "文章解析失败",
                                "http_status": fetch.status_code,
                            }
                        )
                        continue
                    outcome = self.db.upsert_article(
                        site, channel_row, parsed, candidate=candidate
                    )
                    if outcome == "inserted":
                        inserted += 1
                        inserted_urls.add(parsed.url)
                    elif outcome == "updated":
                        updated += 1
                        updated_urls.add(parsed.url)
                    if parsed.extraction_status == "partial":
                        partial += 1
                        errors.append(
                            {
                                "url": candidate.url,
                                "code": "PARTIAL_EXTRACTION",
                                "message": parsed.extraction_error or "正文提取不完整",
                                "http_status": fetch.status_code,
                            }
                        )
                status = "ok" if not errors else "partial"
                if attempted and failed == attempted:
                    status = "error"
        except Exception as exc:  # noqa: BLE001 - every run must return an audit result
            log.exception("refresh failed for %s/%s", site.site_id, channel)
            status = "error"
            errors.append(
                {"code": "REFRESH_EXCEPTION", "message": f"{type(exc).__name__}: {exc}"}
            )

        fetched_at = now_utc()
        result = {
            "ok": status in {"ok", "partial"},
            "status": status,
            "site_id": site.site_id,
            "site_name": site.name,
            "channel": channel,
            "channel_name": channel_row["name"],
            "requested_limit": int(limit),
            "attempted_count": attempted,
            "discovered_count": discovered,
            "inserted_count": inserted,
            "updated_count": updated,
            "failed_count": failed,
            "partial_count": partial,
            "source_mode": source_mode,
            "fetched_at": fetched_at,
            "started_at": started,
            "errors": errors,
            "run_id": run_id,
            "live_urls": sorted(live_urls | inserted_urls | updated_urls),
            "scope": "live_http_fetch",
        }
        self.db.finish_refresh_run(run_id, result)
        self.db.mark_channel_refresh(
            site.site_id,
            channel,
            status,
            ("; ".join(e.get("message", "") for e in errors) or None)
            if status == "error"
            else None,
        )
        return result

    # ------------------------------------------------------------ latest/search
    def get_latest_news(
        self,
        channel: str,
        limit: int = 10,
        refresh: bool = False,
        site_id: str | None = None,
    ) -> dict[str, Any]:
        if not (1 <= int(limit) <= self.registry.settings.max_limit):
            return error_response(
                "INVALID_LIMIT",
                f"limit 必须在 1..{self.registry.settings.max_limit} 之间",
            )
        scope, scope_error = self._read_scope(site_id, default=True)
        if scope_error:
            return scope_error
        refresh_result = None
        live_urls: set[str] = set()
        if refresh:
            if not channel or channel == "all":
                return error_response(
                    "CHANNEL_REQUIRED", "refresh=true 时必须指定具体频道，不能为 all"
                )
            refresh_result = self.refresh_news(channel, limit, site_id=scope)
            live_urls = set(refresh_result.get("live_urls", []))
        channel_filter = None if not channel or channel == "all" else channel
        rows = self.db.list_latest(
            site_id=scope, channel_id=channel_filter, limit=int(limit)
        )
        articles = [self._article_summary(row, live_urls) for row in rows]
        if refresh_result and not refresh_result.get("ok") and not articles:
            return {
                "ok": False,
                "status": "error",
                "data_status": "live_refresh_failed_no_cache",
                "data_source": "none",
                "scope": "local_collected_articles_only",
                "site_id": site_id,
                "channel": channel,
                "articles": [],
                "count": 0,
                "refresh": refresh_result,
                "error_code": refresh_result.get("error_code") or "REFRESH_FAILED",
                "error": "实时采集失败，且本地没有可返回的缓存",
            }
        if refresh_result:
            if refresh_result.get("ok"):
                data_status = "live_refresh_completed"
                data_source = "live_http_fetch_then_sqlite"
            else:
                data_status = "cache_after_refresh_error"
                data_source = "local_sqlite_cache"
        else:
            data_status = "local_cache_only"
            data_source = "local_sqlite_cache"
        return {
            "ok": True,
            "status": "ok",
            "data_status": data_status,
            "data_source": data_source,
            "scope": "local_collected_articles_only",
            "site_id": scope if scope else "all",
            "channel": channel,
            "articles": articles,
            "count": len(articles),
            "refresh": refresh_result,
            "note": (
                "data_status=local_cache_only 表示本次未联网；"
                "live_refresh_completed 表示本次先执行了真实采集，"
                "但返回条目仍来自本地数据库。"
            ),
        }

    def search_news(
        self,
        keyword: str,
        start_date: str | None = None,
        end_date: str | None = None,
        channel: str | None = None,
        limit: int = 10,
        site_id: str | None = None,
    ) -> dict[str, Any]:
        keyword = (keyword or "").strip()
        if not keyword:
            return error_response("INVALID_KEYWORD", "keyword 不能为空")
        if not (1 <= int(limit) <= self.registry.settings.max_limit):
            return error_response(
                "INVALID_LIMIT",
                f"limit 必须在 1..{self.registry.settings.max_limit} 之间",
            )
        scope, scope_error = self._read_scope(site_id, default=False)
        if scope_error:
            return scope_error
        try:
            start_utc, end_utc = search_bounds(
                start_date, end_date, self.registry.settings.search_timezone
            )
        except DateParseError as exc:
            return error_response("INVALID_DATE", str(exc))
        total, rows = self.db.search(
            keyword=keyword,
            start_utc=start_utc,
            end_utc=end_utc,
            site_id=scope,
            channel_id=None if not channel or channel == "all" else channel,
            limit=int(limit),
        )
        return {
            "ok": True,
            "status": "ok",
            "scope": "local_collected_articles_only",
            "disclaimer": (
                "本工具只检索本地已采集数据库，不代表已搜索新华网或任何网站的全站、"
                "全部历史新闻；返回结果受采集时间和频道范围限制。"
            ),
            "keyword": keyword,
            "start_date": start_date,
            "end_date": end_date,
            "channel": channel,
            "site_id": scope if scope else "all",
            "count": len(rows),
            "total_matches": total,
            "results": [self._article_summary(row, set()) for row in rows],
        }

    # --------------------------------------------------------------- article
    def get_article(self, url: str, site_id: str | None = None) -> dict[str, Any]:
        fetched_at = now_utc()
        try:
            resolved = self.resolve_article_url(url, site_id)
        except UrlValidationError as exc:
            return {
                "ok": False,
                "status": "rejected",
                "title": None,
                "url": url,
                "original_url": url,
                "published_at": None,
                "published_at_local": None,
                "content": None,
                "source": None,
                "fetched_at": fetched_at,
                "data_status": "rejected_before_network",
                "error_code": exc.code,
                "error": exc.message,
            }
        site = resolved.site
        adapter = self._adapter(site)
        fetch = adapter.fetch_article(resolved.url)
        if not fetch.ok:
            return {
                "ok": False,
                "status": "failed",
                "title": None,
                "url": resolved.url,
                "original_url": url,
                "published_at": None,
                "published_at_local": None,
                "content": None,
                "source": site.name,
                "fetched_at": fetched_at,
                "site_id": site.site_id,
                "site_name": site.name,
                "channel": resolved.channel_id,
                "data_status": "live_fetch_failed",
                "http_status": fetch.status_code,
                "error_code": fetch.error_code or "FETCH_FAILED",
                "error": fetch.error or "文章抓取失败",
            }
        parsed = adapter.parse_article(
            fetch,
            channel_id=resolved.channel_id,
            candidate=Candidate(
                url=resolved.url,
                title="",
                channel_id=resolved.channel_id,
                source_mode="direct_url",
            ),
        )
        if parsed.title:
            channel_row = self.db.get_channel(site.site_id, resolved.channel_id)
            if channel_row is None:
                channel_row = {
                    "channel_id": resolved.channel_id,
                    "name": resolved.channel_name,
                }
            self.db.upsert_article(site, channel_row, parsed)
        return {
            "ok": parsed.extraction_status != "failed",
            "status": parsed.extraction_status,
            "title": parsed.title,
            "url": parsed.url,
            "original_url": url,
            "published_at": parsed.published_at,
            "published_at_local": parsed.published_at_local,
            "content": parsed.content,
            "source": parsed.source or site.name,
            "site_id": site.site_id,
            "site_name": site.name,
            "channel": resolved.channel_id,
            "fetched_at": parsed.fetched_at,
            "data_status": "live_http_fetch",
            "http_status": fetch.status_code,
            "content_chars": len("".join((parsed.content or "").split())),
            "error_code": None if parsed.extraction_status == "ok" else "EXTRACTION_PARTIAL",
            "error": parsed.extraction_error,
        }

    def resolve_article_url(self, url: str, site_id: str | None = None) -> ResolvedArticle:
        candidates: list[ResolvedArticle] = []
        for site in self.registry.sites.values():
            if not site.enabled:
                continue
            if site_id and site_id not in {"all", site.site_id}:
                continue
            try:
                check = validate_article_url(
                    url,
                    allowed_domains=site.allowed_domains,
                    patterns=site.article_url_patterns,
                    host_aliases=site.host_aliases,
                    check_dns=False,
                )
            except UrlValidationError:
                continue
            channel_id, channel_name = self._channel_for_url(site, check.url)
            candidates.append(
                ResolvedArticle(
                    site=site,
                    url=check.url,
                    channel_id=channel_id,
                    channel_name=channel_name,
                )
            )
        if not candidates:
            if site_id and site_id not in {"all"} and site_id not in self.registry.sites:
                raise UrlValidationError("UNKNOWN_SITE", f"未知站点: {site_id}")
            raise UrlValidationError(
                "URL_NOT_ALLOWED",
                "URL 不属于任何已注册站点，或不符合其文章链接规则，已拒绝",
            )
        candidates.sort(key=lambda item: len(item.site.site_id), reverse=True)
        return candidates[0]

    def _channel_for_url(self, site: SiteConfig, url: str) -> tuple[str, str]:
        path = urlsplit(url).path
        for channel in site.channels:
            channel_path = urlsplit(channel.url).path.rstrip("/")
            if channel_path and (path.startswith(channel_path + "/") or path == channel_path):
                return channel.id, channel.name
        # Article URL path's first segment usually maps to a channel.
        first = path.strip("/").split("/", 1)[0]
        if first and first != path.strip("/"):
            for channel in site.channels:
                if first in urlsplit(channel.url).path:
                    return channel.id, channel.name
        enabled = [c for c in site.channels if c.enabled]
        if len(enabled) == 1:
            return enabled[0].id, enabled[0].name
        return "article", "文章"

    # --------------------------------------------------------------- validate
    def validate_site(self, site_id: str) -> dict[str, Any]:
        site = self.registry.get(site_id)
        if site is None:
            return error_response("UNKNOWN_SITE", f"未知站点: {site_id}")
        if not site.enabled:
            return error_response("SITE_DISABLED", f"站点已禁用: {site_id}")
        adapter = self._adapter(site)
        try:
            report = adapter.validate_source()
        except Exception as exc:  # noqa: BLE001
            log.exception("validate_site failed for %s", site_id)
            return error_response(
                "VALIDATION_EXCEPTION", f"{type(exc).__name__}: {exc}"
            )
        self.db.update_validation(site_id, report)
        report["ok"] = bool(report.get("valid_channels"))
        report["status"] = "valid" if report["ok"] else "failed"
        report["validated_only"] = True
        report["note"] = (
            "验证会真实访问栏目页和文章页；只有至少一个频道有可解析正文的样本，"
            "该频道才会被 list_channels 返回。"
        )
        return report

    # ---------------------------------------------------------------- inspect
    def inspect_site(self, url: str) -> dict[str, Any]:
        inspector = SiteInspector()
        return inspector.inspect(url)

    # ---------------------------------------------------------------- helpers
    def _site_for_write(
        self, site_id: str | None
    ) -> tuple[SiteConfig | None, dict[str, Any] | None]:
        requested = site_id or self.registry.settings.default_site
        if requested == "all":
            return None, error_response(
                "SITE_REQUIRED", "refresh/validate 操作必须指定具体 site_id，不能为 all"
            )
        site = self.registry.get(requested)
        if site is None:
            return None, error_response("UNKNOWN_SITE", f"未知站点: {requested}")
        if not site.enabled:
            return None, error_response("SITE_DISABLED", f"站点已禁用: {requested}")
        return site, None

    def _read_scope(
        self, site_id: str | None, *, default: bool
    ) -> tuple[str | None, dict[str, Any] | None]:
        if site_id is None and default:
            site_id = self.registry.settings.default_site
        if site_id in (None, "all"):
            return None, None
        if site_id not in self.registry.sites:
            return None, error_response("UNKNOWN_SITE", f"未知站点: {site_id}")
        return site_id, None

    def _article_summary(self, row: dict[str, Any], live_urls: set[str]) -> dict[str, Any]:
        url = row["url"]
        is_live = url in live_urls
        return {
            "site_id": row["site_id"],
            "site_name": row["site_name"],
            "channel": row["channel_id"],
            "channel_name": row["channel_name"],
            "title": row["title"],
            "url": url,
            "original_url": row.get("original_url") or url,
            "published_at": row["published_at"],
            "published_at_local": row["published_at_local"],
            "published_date": row["published_date"],
            "source": row["source"],
            "fetched_at": row["fetched_at"],
            "extraction_status": row["extraction_status"],
            "content_chars": row["content_chars"],
            "data_status": "live" if is_live else "cached",
            "cache_age_seconds": _age_seconds(row["fetched_at"]),
        }
