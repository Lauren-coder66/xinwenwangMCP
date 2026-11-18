"""Polite HTTP client: robots.txt, per-domain rate limit, redirect/SSRF checks."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import SiteConfig
from .models import FetchResult
from .urls import (
    UrlValidationError,
    host_allowed,
    normalize_url,
    resolve_public_host,
)

log = logging.getLogger(__name__)

HTML_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/json",
}


@dataclass(slots=True)
class RobotsEntry:
    parser: RobotFileParser | None
    allowed_all: bool
    disallow_all: bool
    status_code: int | None
    error: str | None = None
    error_code: str | None = None


class DomainRateLimiter:
    def __init__(self, sleeper=time.sleep, clock=time.monotonic):
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global = threading.Lock()
        self._sleeper = sleeper
        self._clock = clock

    def wait(self, domain: str, delay: float) -> None:
        with self._global:
            lock = self._locks.setdefault(domain, threading.Lock())
        with lock:
            now = self._clock()
            last = self._last.get(domain)
            if last is not None:
                remaining = delay - (now - last)
                if remaining > 0:
                    self._sleeper(remaining)
            self._last[domain] = self._clock()


class HttpClient:
    def __init__(
        self,
        site: SiteConfig,
        *,
        default_user_agent: str,
        transport: httpx.BaseTransport | None = None,
        offline: bool | None = None,
        check_dns: bool = True,
    ):
        self.site = site
        self.user_agent = site.user_agent or default_user_agent
        self._check_dns = check_dns
        self._offline = (
            os.environ.get("NEWS_MCP_OFFLINE", "").lower() in {"1", "true", "yes"}
            if offline is None
            else offline
        )
        self._client = httpx.Client(
            timeout=httpx.Timeout(site.timeout_seconds),
            follow_redirects=False,
            transport=transport,
            trust_env=site.trust_env,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
        )
        self._limiter = DomainRateLimiter()
        self._robots: dict[str, RobotsEntry] = {}
        self._robots_lock = threading.Lock()
        self._request_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def get(
        self,
        url: str,
        *,
        allowed_domains: list[str] | None = None,
        patterns: list[str] | None = None,
        check_robots: bool = True,
        allow_content_types: set[str] | None = None,
        check_dns: bool | None = None,
    ) -> FetchResult:
        started = time.monotonic()
        domains = allowed_domains or self.site.allowed_domains
        if check_dns is None:
            check_dns = self._check_dns
        try:
            current = normalize_url(
                url,
                host_aliases=self.site.host_aliases,
                strip_trailing_slash=False,
            )
        except UrlValidationError as exc:
            return FetchResult(
                url=url,
                error_code=exc.code,
                error=exc.message,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        if self._offline:
            return FetchResult(
                url=current,
                error_code="NETWORK_DISABLED",
                error="网络请求被禁用（NEWS_MCP_OFFLINE=1）",
                elapsed_ms=0,
            )

        redirects = 0
        while True:
            parts = urlsplit(current)
            if not host_allowed(parts.hostname or "", domains):
                return FetchResult(
                    url=url,
                    final_url=current,
                    error_code="HOST_NOT_ALLOWED",
                    error=f"域名不在白名单: {parts.hostname}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            try:
                if check_dns:
                    resolve_public_host(parts.hostname or "", parts.port or 443)
            except UrlValidationError as exc:
                return FetchResult(
                    url=url,
                    final_url=current,
                    error_code=exc.code,
                    error=exc.message,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )

            if check_robots and self.site.respect_robots:
                allowed, robots_error, robots_code = self._robots_allowed(current)
                if not allowed:
                    return FetchResult(
                        url=url,
                        final_url=current,
                        error_code=robots_code or "ROBOTS_DISALLOWED",
                        error=robots_error or "robots.txt 不允许抓取该 URL",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )

            self._limiter.wait(parts.hostname or "", self.site.request_delay_seconds)
            try:
                with self._request_lock:
                    response = self._client.get(current)
            except httpx.TimeoutException as exc:
                return FetchResult(
                    url=url,
                    final_url=current,
                    error_code="FETCH_TIMEOUT",
                    error=f"请求超时: {exc}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except httpx.ConnectError as exc:
                return FetchResult(
                    url=url,
                    final_url=current,
                    error_code="FETCH_CONNECT_ERROR",
                    error=f"连接失败: {exc}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            except httpx.HTTPError as exc:
                return FetchResult(
                    url=url,
                    final_url=current,
                    error_code="FETCH_ERROR",
                    error=f"HTTP 客户端错误: {exc}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return FetchResult(
                        url=url,
                        final_url=current,
                        status_code=response.status_code,
                        error_code="BAD_REDIRECT",
                        error="重定向缺少 Location",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                if redirects >= self.site.max_redirects:
                    return FetchResult(
                        url=url,
                        final_url=current,
                        status_code=response.status_code,
                        error_code="TOO_MANY_REDIRECTS",
                        error=f"重定向次数超过 {self.site.max_redirects}",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                try:
                    target = normalize_url(
                        location,
                        base=current,
                        host_aliases=self.site.host_aliases,
                        strip_trailing_slash=False,
                    )
                    target_host = urlsplit(target).hostname or ""
                    if not host_allowed(target_host, domains):
                        raise UrlValidationError(
                            "REDIRECT_HOST_NOT_ALLOWED",
                            f"跳转目标域名不在白名单，已拒绝: {target_host}",
                        )
                    resolve_public_host(target_host, urlsplit(target).port or 443)
                    if patterns and not any(
                        re.fullmatch(pattern, target) for pattern in patterns
                    ):
                        raise UrlValidationError(
                            "REDIRECT_PATTERN_MISMATCH",
                            "跳转目标不符合该站点文章规则，已拒绝",
                        )
                except UrlValidationError as exc:
                    return FetchResult(
                        url=url,
                        final_url=current,
                        status_code=response.status_code,
                        error_code=exc.code,
                        error=exc.message,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    )
                current = target
                redirects += 1
                continue

            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            allowed_types = allow_content_types or HTML_TYPES
            if content_type and content_type not in allowed_types:
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    error_code="UNSUPPORTED_CONTENT_TYPE",
                    error=f"不支持的内容类型: {content_type}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            if len(response.content) > self.site.max_response_bytes:
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    error_code="RESPONSE_TOO_LARGE",
                    error=f"响应超过 {self.site.max_response_bytes} 字节",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            if response.status_code != 200:
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    error_code=f"HTTP_{response.status_code}",
                    error=f"HTTP {response.status_code}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            try:
                text = response.text
            except Exception as exc:  # noqa: BLE001
                return FetchResult(
                    url=url,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    error_code="DECODE_ERROR",
                    error=f"响应解码失败: {exc}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            return FetchResult(
                url=url,
                final_url=current,
                status_code=response.status_code,
                text=text,
                content_type=content_type,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

    def _robots_allowed(self, url: str) -> tuple[bool, str | None, str | None]:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        with self._robots_lock:
            entry = self._robots.get(origin)
        if entry is None:
            entry = self._fetch_robots(origin)
            with self._robots_lock:
                self._robots[origin] = entry
        if entry.disallow_all:
            return (
                False,
                entry.error or f"robots.txt 禁止访问（HTTP {entry.status_code}）",
                entry.error_code or "ROBOTS_DISALLOWED",
            )
        if entry.allowed_all or entry.parser is None:
            return True, None, None
        ua = self.user_agent
        allowed = entry.parser.can_fetch(ua, url)
        if not allowed:
            return False, "robots.txt 明确禁止抓取该 URL", "ROBOTS_DISALLOWED"
        return True, None, None

    def _fetch_robots(self, origin: str) -> RobotsEntry:
        robots_url = origin + "robots.txt"
        parts = urlsplit(origin)
        try:
            if self._check_dns:
                resolve_public_host(parts.hostname or "", parts.port or 443)
            self._limiter.wait(parts.hostname or "", self.site.request_delay_seconds)
            with self._request_lock:
                response = self._client.get(robots_url)
        except httpx.TimeoutException as exc:
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, None, f"robots 获取超时，策略 open: {exc}", "FETCH_TIMEOUT")
            return RobotsEntry(None, False, True, None, f"robots 获取超时，策略 closed: {exc}", "FETCH_TIMEOUT")
        except httpx.ConnectError as exc:
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, None, f"robots 连接失败，策略 open: {exc}", "FETCH_CONNECT_ERROR")
            return RobotsEntry(None, False, True, None, f"robots 连接失败，策略 closed: {exc}", "FETCH_CONNECT_ERROR")
        except Exception as exc:  # noqa: BLE001
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, None, f"robots 获取失败，策略 open: {exc}", "ROBOTS_FETCH_ERROR")
            return RobotsEntry(None, False, True, None, f"robots 获取失败，策略 closed: {exc}", "ROBOTS_FETCH_ERROR")

        if response.status_code in (401, 403):
            return RobotsEntry(None, False, True, response.status_code, "robots 拒绝访问", "ROBOTS_DISALLOWED")
        if response.status_code >= 400:
            # RFC 9309: 404 means no restrictions; other 4xx unavailable.
            if response.status_code == 404:
                return RobotsEntry(None, True, False, 404)
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, response.status_code)
            return RobotsEntry(None, False, True, response.status_code, "robots 不可用", "ROBOTS_UNAVAILABLE")
        content_type = (response.headers.get("content-type") or "").lower()
        if "html" in content_type:
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, response.status_code)
            return RobotsEntry(
                None, False, True, response.status_code, "robots 返回 HTML 而非文本", "ROBOTS_INVALID"
            )
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.parse(response.text.lstrip("\ufeff").splitlines())
        except Exception as exc:  # noqa: BLE001
            if self.site.robots_failure_policy == "open":
                return RobotsEntry(None, True, False, response.status_code, str(exc), "ROBOTS_INVALID")
            return RobotsEntry(None, False, True, response.status_code, str(exc), "ROBOTS_INVALID")
        return RobotsEntry(parser, False, False, response.status_code)
