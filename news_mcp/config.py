"""YAML configuration models and a fault-isolating loader.

A broken site file is reported and skipped. It never prevents other sites from
loading, and it never modifies or deletes existing article data.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

log = logging.getLogger(__name__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscoveryConfig(StrictModel):
    type: Literal["rss", "sitemap", "html", "json"]
    url: str | None = None
    link_selector: str = "a[href]"
    url_pattern: str | None = None
    max_age_days: int | None = Field(default=None, ge=1, le=3650)
    max_items: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float | None = Field(default=None, ge=1, le=120)
    same_channel_path: bool = True
    # JSON discovery (official policy portals often expose a static JSON list)
    items_path: str | None = None
    title_key: str = "title"
    url_key: str = "url"
    date_key: str | None = None


class ArticleRules(StrictModel):
    title_selectors: list[str] = Field(default_factory=lambda: ["h1 .title", "h1"])
    published_at_selectors: list[str] = Field(
        default_factory=lambda: [".info", ".header-time", ".time", "time"]
    )
    date_meta_names: list[str] = Field(
        default_factory=lambda: [
            "publishdate",
            "pubdate",
            "date",
            "article:published_time",
            "firstpublishedtime",
            "lastmodifiedtime",
        ]
    )
    datetime_regex: str = (
        r"(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?"
        r"(?:\s*[T ]?\s*\d{1,2}:\d{2}(?::\d{2})?)?)"
    )
    source_selectors: list[str] = Field(default_factory=lambda: [".source"])
    author_selectors: list[str] = Field(default_factory=lambda: [".author", "[rel=author]"])
    summary_meta_names: list[str] = Field(default_factory=lambda: ["description", "og:description"])
    content_selectors: list[str] = Field(
        default_factory=lambda: ["#detailContent", "article", ".article-content", ".content"]
    )
    paragraph_selector: str = "p"
    remove_selectors: list[str] = Field(
        default_factory=lambda: [
            "script",
            "style",
            "noscript",
            "iframe",
            ".ad",
            ".advert",
            ".advertisement",
            ".recommend",
            ".related",
            ".share",
            ".qrcode",
            ".editor",
            ".copyright",
        ]
    )
    strip_title_suffixes: list[str] = Field(
        default_factory=lambda: ["-新华网", "_新华网", "—新华网", "- 新华网"]
    )
    min_content_chars: int = Field(default=80, ge=0, le=100000)
    allow_generic_content_fallback: bool = True
    generic_content_selectors: list[str] = Field(
        default_factory=lambda: [
            "article",
            "[itemprop=articleBody]",
            ".article-content",
            ".article_body",
            ".post-content",
            ".content",
            "main",
        ]
    )

    @field_validator("title_selectors", "content_selectors", "generic_content_selectors")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("选择器列表不能为空")
        return value

    @field_validator("datetime_regex")
    @classmethod
    def _valid_regex(cls, value: str) -> str:
        re.compile(value)
        return value


class ChannelConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    name: str = Field(min_length=1, max_length=80)
    url: str
    enabled: bool = True
    discovery: list[DiscoveryConfig] | None = None
    article: ArticleRules | None = None
    note: str | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not re.match(r"^https?://", value):
            raise ValueError("频道 URL 必须是 http(s)")
        return value


class ValidationConfig(StrictModel):
    sample_articles: int = Field(default=3, ge=1, le=10)
    min_ok_articles: int = Field(default=1, ge=1, le=10)
    max_channels: int = Field(default=20, ge=1, le=100)


class SiteConfig(StrictModel):
    site_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    name: str = Field(min_length=1, max_length=120)
    homepage: str
    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    html_parser: Literal["lxml", "html.parser"] = "lxml"
    allowed_domains: list[str] = Field(min_length=1)
    host_aliases: dict[str, str] = Field(default_factory=dict)
    article_url_patterns: list[str] = Field(min_length=1)
    request_delay_seconds: float = Field(default=1.5, ge=0.5, le=60)
    timeout_seconds: float = Field(default=25, ge=3, le=120)
    max_redirects: int = Field(default=3, ge=0, le=10)
    max_response_bytes: int = Field(default=4_000_000, ge=100_000, le=20_000_000)
    trust_env: bool = True
    respect_robots: bool = True
    robots_failure_policy: Literal["closed", "open"] = "closed"
    user_agent: str | None = None
    custom_adapter: str | None = None
    discovery_note: str | None = None
    channels: list[ChannelConfig] = Field(min_length=1)
    article: ArticleRules = Field(default_factory=ArticleRules)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    @field_validator("homepage")
    @classmethod
    def _homepage_http(cls, value: str) -> str:
        if not re.match(r"^https?://", value):
            raise ValueError("homepage 必须是 http(s)")
        return value

    @field_validator("article_url_patterns")
    @classmethod
    def _patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            re.compile(pattern)
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _domains_normalise(cls, value: list[str]) -> list[str]:
        result = []
        for item in value:
            domain = item.lower().strip().lstrip(".").rstrip(".")
            if not domain or "/" in domain:
                raise ValueError(f"无效域名: {item!r}")
            result.append(domain)
        return result

    @model_validator(mode="after")
    def _unique_channels(self) -> "SiteConfig":
        ids = [c.id for c in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError("同一站点内 channel id 必须唯一")
        for channel in self.channels:
            if channel.discovery is None:
                # A channel may inherit the default: its own HTML page.
                object.__setattr__(
                    channel,
                    "discovery",
                    [
                        DiscoveryConfig(
                            type="html",
                            url=channel.url,
                            url_pattern=self.article_url_patterns[0],
                        )
                    ],
                )
        return self

    def rules_for(self, channel_id: str) -> ArticleRules:
        for channel in self.channels:
            if channel.id == channel_id:
                return channel.article or self.article
        return self.article

    def channel(self, channel_id: str) -> ChannelConfig | None:
        return next((c for c in self.channels if c.id == channel_id), None)

    @property
    def config_hash(self) -> str:
        payload = self.model_dump_json(exclude_none=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class Settings(StrictModel):
    default_site: str = "xinhua"
    default_limit: int = Field(default=10, ge=1, le=50)
    max_limit: int = Field(default=50, ge=1, le=200)
    search_timezone: str = "Asia/Shanghai"
    user_agent: str = (
        "news-mcp/1.0 (+https://example.invalid/news-mcp; respectful collector; "
        "contact via local configuration)"
    )
    log_level: str = "INFO"


class ConfigRegistry:
    def __init__(self, sites: dict[str, SiteConfig], settings: Settings, root: Path):
        self.sites = sites
        self.settings = settings
        self.root = root
        self.errors: list[dict[str, str]] = []

    def enabled_sites(self) -> list[SiteConfig]:
        return [s for s in self.sites.values() if s.enabled]

    def get(self, site_id: str) -> SiteConfig | None:
        return self.sites.get(site_id)


def default_config_dir() -> Path:
    env = os.environ.get("NEWS_MCP_CONFIG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "config",
        here.parent.parent / "config",
        Path.cwd() / "config",
    ]
    for candidate in candidates:
        if (candidate / "settings.yaml").exists():
            return candidate
    return here.parent.parent / "config"


def load_registry(config_dir: str | Path | None = None) -> ConfigRegistry:
    root = Path(config_dir or default_config_dir()).resolve()
    settings_path = root / "settings.yaml"
    errors: list[dict[str, str]] = []
    settings = Settings()
    if settings_path.exists():
        try:
            raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            settings = Settings.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - report, fall back to defaults
            errors.append(
                {
                    "file": str(settings_path),
                    "site_id": "settings",
                    "error": f"settings.yaml 无效，已使用默认设置: {exc}",
                }
            )
    else:
        errors.append(
            {
                "file": str(settings_path),
                "site_id": "settings",
                "error": "未找到 settings.yaml，已使用默认设置",
            }
        )

    sites: dict[str, SiteConfig] = {}
    sites_dir = root / "sites"
    for path in sorted(sites_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("顶层必须是 YAML 映射")
            site = SiteConfig.model_validate(raw)
            if site.site_id in sites:
                raise ValueError(f"重复 site_id: {site.site_id}")
            sites[site.site_id] = site
            log.info("loaded site config %s (%s)", site.site_id, path.name)
        except Exception as exc:  # noqa: BLE001 - isolate one bad site config
            errors.append(
                {
                    "file": str(path),
                    "site_id": path.stem,
                    "error": f"配置无效，已跳过该站点: {exc}",
                }
            )
            log.error("invalid site config %s: %s", path, exc)
    registry = ConfigRegistry(sites, settings, root)
    registry.errors = errors
    return registry


def read_config_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def site_config_path(registry: ConfigRegistry, site_id: str) -> Path | None:
    for path in (registry.root / "sites").glob("*.yaml"):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if raw.get("site_id") == site_id:
                return path
        except Exception:  # noqa: BLE001
            continue
    return None


def mask_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Utility for diagnostics: never print obvious credentials."""
    masked = {}
    for key, value in data.items():
        if any(token in key.lower() for token in ("token", "secret", "password", "key")):
            masked[key] = "***"
        else:
            masked[key] = value
    return masked
