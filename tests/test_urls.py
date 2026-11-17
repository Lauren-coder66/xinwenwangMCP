from __future__ import annotations

import pytest

from news_mcp.urls import (
    UrlValidationError,
    check_redirect_target,
    host_allowed,
    normalize_url,
    resolve_public_host,
    validate_article_url,
)

PATTERN = r"^https://(?:www\.)?news\.cn/politics/20\d{6}/[0-9a-f]{32}/c\.html$"
URL = "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html"


def test_normalize_url_drops_tracking_and_fragment():
    raw = (
        "http://WWW.NEWS.CN/politics/20261117/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html?utm_source=x&b=2&a=1#top"
    )
    assert normalize_url(raw) == URL + "?a=1&b=2"


def test_normalize_url_page_one_is_default_page():
    raw = URL + "?page=1"
    assert normalize_url(raw) == URL


def test_normalize_url_keeps_page_two():
    raw = URL + "?page=2"
    assert normalize_url(raw) == URL + "?page=2"


def test_normalize_url_rejects_userinfo_and_ports():
    with pytest.raises(UrlValidationError) as exc:
        normalize_url("https://user:pass@www.news.cn/a")
    assert exc.value.code == "USERINFO_NOT_ALLOWED"
    with pytest.raises(UrlValidationError) as exc2:
        normalize_url("https://www.news.cn:8443/a")
    assert exc2.value.code == "PORT_NOT_ALLOWED"


def test_host_allowlist_is_boundary_safe():
    assert host_allowed("www.news.cn", ["news.cn"])
    assert host_allowed("news.cn", ["news.cn"])
    assert not host_allowed("evilnews.cn", ["news.cn"])
    assert not host_allowed("news.cn.evil.example", ["news.cn"])


def test_validate_article_url_accepts_valid():
    check = validate_article_url(
        URL,
        allowed_domains=["news.cn"],
        patterns=[PATTERN],
        check_dns=False,
    )
    assert check.host == "www.news.cn"


def test_validate_article_url_rejects_non_whitelist():
    with pytest.raises(UrlValidationError) as exc:
        validate_article_url(
            "https://example.com/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html",
            allowed_domains=["news.cn"],
            patterns=[PATTERN],
            check_dns=False,
        )
    assert exc.value.code == "HOST_NOT_ALLOWED"


def test_validate_article_url_rejects_pattern_mismatch():
    with pytest.raises(UrlValidationError) as exc:
        validate_article_url(
            "https://www.news.cn/fake/2026/11/17/1.html",
            allowed_domains=["news.cn"],
            patterns=[PATTERN],
            check_dns=False,
        )
    assert exc.value.code == "URL_PATTERN_MISMATCH"


def test_redirect_to_non_whitelist_is_rejected_before_request():
    with pytest.raises(UrlValidationError) as exc:
        check_redirect_target(
            URL,
            "https://169.254.169.254/latest/meta-data",
            allowed_domains=["news.cn"],
            patterns=[PATTERN],
        )
    assert exc.value.code == "REDIRECT_HOST_NOT_ALLOWED"


def test_private_literal_address_rejected():
    with pytest.raises(UrlValidationError) as exc:
        resolve_public_host("127.0.0.1")
    assert exc.value.code == "PRIVATE_ADDRESS"
