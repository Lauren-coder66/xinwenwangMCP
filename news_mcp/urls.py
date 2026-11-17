"""URL canonicalisation, allow-listing and SSRF guards."""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "from",
    "source",
    "spm",
    "share",
    "share_source",
    "share_medium",
    "share_plat",
    "share_session_id",
    "share_tag",
    "timestamp",
    "ref",
    "referer",
    "referrer",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "yclid",
    "igshid",
}
_ALLOWED_PORTS = {None, 80, 443}


class UrlValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class UrlCheck:
    url: str
    scheme: str
    host: str
    port: int | None


def normalize_url(
    url: str,
    *,
    base: str | None = None,
    host_aliases: dict[str, str] | None = None,
    force_https: bool = True,
    strip_trailing_slash: bool = True,
) -> str:
    """Canonical URL used for deduplication.

    Removes fragments and tracking query parameters, sorts remaining query
    parameters, lowercases host, removes default ports and normalises slashes.
    It does not silently discard meaningful query parameters.
    """

    raw = (url or "").strip()
    if base:
        raw = urljoin(base, raw)
    if not raw:
        raise UrlValidationError("EMPTY_URL", "URL 为空")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in {"http", "https"}:
        raise UrlValidationError("UNSUPPORTED_SCHEME", f"不支持的协议: {parts.scheme!r}")
    if parts.username or parts.password:
        raise UrlValidationError("USERINFO_NOT_ALLOWED", "URL 不得包含用户名或密码")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UrlValidationError("MISSING_HOST", "URL 缺少主机名")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlValidationError("INVALID_PORT", f"URL 端口无效: {exc}") from exc
    if port not in _ALLOWED_PORTS:
        raise UrlValidationError("PORT_NOT_ALLOWED", f"不允许的端口: {port}")
    if host_aliases:
        host = host_aliases.get(host, host).lower().rstrip(".")
    scheme = "https" if force_https else parts.scheme.lower()

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if strip_trailing_slash and path != "/":
        path = path.rstrip("/")

    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower in TRACKING_PARAMS or lower.startswith("utm_"):
            continue
        if lower == "page" and value == "1":
            # page=1 is equivalent to the unpaginated canonical article.
            continue
        pairs.append((key, value))
    pairs.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(pairs, doseq=True)

    netloc = host
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{host}:{port}"
    return urlunsplit((scheme, netloc, path, query, ""))


def host_allowed(host: str, allowed_domains: list[str] | tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    for domain in allowed_domains:
        d = domain.lower().strip().lstrip(".").rstrip(".")
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_global) and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolve_public_host(host: str, port: int = 443) -> list[str]:
    """Resolve a host and reject private/loopback/link-local targets.

    DNS rebinding is mitigated by re-checking redirects and by connecting to a
    normal public host only. The system resolver is used so corporate DNS still
    works; literal private addresses are always rejected.
    """

    if not host:
        raise UrlValidationError("MISSING_HOST", "URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UrlValidationError("DNS_ERROR", f"域名解析失败 {host}: {exc}") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise UrlValidationError("DNS_ERROR", f"域名无解析结果: {host}")
    bad = [addr for addr in addresses if not is_public_ip(addr)]
    if bad:
        raise UrlValidationError(
            "PRIVATE_ADDRESS", f"域名解析到非公网地址，已拒绝: {host} -> {', '.join(bad)}"
        )
    return addresses


def validate_article_url(
    url: str,
    *,
    allowed_domains: list[str],
    patterns: list[str],
    host_aliases: dict[str, str] | None = None,
    force_https: bool = True,
    check_dns: bool = True,
) -> UrlCheck:
    normalized = normalize_url(
        url, host_aliases=host_aliases, force_https=force_https
    )
    parts = urlsplit(normalized)
    if not host_allowed(parts.hostname or "", allowed_domains):
        raise UrlValidationError(
            "HOST_NOT_ALLOWED",
            f"域名不在白名单: {parts.hostname}; 允许: {', '.join(allowed_domains)}",
        )
    if patterns and not any(re.fullmatch(pattern, normalized) for pattern in patterns):
        raise UrlValidationError(
            "URL_PATTERN_MISMATCH", "URL 不符合该站点文章链接规则，已拒绝"
        )
    if check_dns:
        resolve_public_host(parts.hostname or "", parts.port or 443)
    return UrlCheck(
        url=normalized,
        scheme=parts.scheme,
        host=parts.hostname or "",
        port=parts.port,
    )


def check_redirect_target(
    current: str,
    location: str,
    *,
    allowed_domains: list[str],
    patterns: list[str] | None = None,
    host_aliases: dict[str, str] | None = None,
) -> str:
    """Validate every redirect hop before it is requested."""

    target = normalize_url(location, base=current, host_aliases=host_aliases)
    parts = urlsplit(target)
    if not host_allowed(parts.hostname or "", allowed_domains):
        raise UrlValidationError(
            "REDIRECT_HOST_NOT_ALLOWED",
            f"跳转目标域名不在白名单，已拒绝: {parts.hostname}",
        )
    resolve_public_host(parts.hostname or "", parts.port or 443)
    if patterns and not any(re.fullmatch(pattern, target) for pattern in patterns):
        raise UrlValidationError(
            "REDIRECT_PATTERN_MISMATCH", "跳转目标不符合文章链接规则，已拒绝"
        )
    return target
