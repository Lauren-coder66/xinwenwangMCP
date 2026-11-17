from __future__ import annotations

import httpx

from news_mcp.fetch import HttpClient

URL = "https://www.news.cn/politics/20261117/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html"


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
    if request.url.path.endswith("/timeout/c.html"):
        raise httpx.ReadTimeout("simulated timeout", request=request)
    if request.url.path.endswith("/missing/c.html"):
        return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
    if request.url.path.endswith("/redirect/c.html"):
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
        )
    return httpx.Response(200, text="ok", headers={"content-type": "text/html"})


def _client(xinhua_site, **kwargs) -> HttpClient:
    return HttpClient(
        xinhua_site,
        default_user_agent="test",
        transport=httpx.MockTransport(_handler),
        check_dns=False,
        **kwargs,
    )


def test_timeout_is_reported_not_swallowed(xinhua_site):
    client = _client(xinhua_site)
    try:
        result = client.get(URL.replace("/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html", "/timeout/c.html"))
        assert result.error_code == "FETCH_TIMEOUT"
        assert "超时" in (result.error or "")
    finally:
        client.close()


def test_http_404_is_reported(xinhua_site):
    client = _client(xinhua_site)
    try:
        result = client.get(URL.replace("/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html", "/missing/c.html"))
        assert result.error_code == "HTTP_404"
        assert result.status_code == 404
    finally:
        client.close()


def test_redirect_to_private_host_is_rejected(xinhua_site):
    client = _client(xinhua_site)
    try:
        result = client.get(URL.replace("/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/c.html", "/redirect/c.html"))
        assert result.error_code == "REDIRECT_HOST_NOT_ALLOWED"
    finally:
        client.close()


def test_robots_server_error_is_not_treated_as_allow(xinhua_site):
    def robots_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error", headers={"content-type": "text/plain"})

    client = HttpClient(
        xinhua_site,
        default_user_agent="test",
        transport=httpx.MockTransport(robots_error),
        check_dns=False,
    )
    try:
        result = client.get(URL)
        assert result.error_code == "ROBOTS_UNAVAILABLE"
    finally:
        client.close()
