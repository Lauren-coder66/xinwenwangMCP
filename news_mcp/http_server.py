"""Remote MCP transport for ChatGPT / Doubao / other URL-only clients.

The stdio server remains the local default. This module serves the same tools
over the MCP Streamable HTTP transport so a cloud client can reach it through
a public HTTPS URL (reverse proxy or tunnel).

Security defaults:
- binds to 127.0.0.1, not 0.0.0.0;
- optional bearer token via NEWS_MCP_HTTP_TOKEN;
- optional Host allow-list via NEWS_MCP_ALLOWED_HOSTS.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .server import mcp

log = logging.getLogger(__name__)

DEFAULT_PATH = "/mcp"
DEFAULT_PORT = 8765


class BearerTokenMiddleware:
    """Tiny ASGI auth gate; does not buffer MCP streaming responses."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        supplied = headers.get("x-api-key", "")
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if supplied != self.token:
            body = json.dumps(
                {"error": "unauthorized", "message": "Missing or invalid MCP bearer token"},
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


async def healthz(request: Request) -> Response:
    return JSONResponse(
        {
            "ok": True,
            "service": "news-mcp",
            "version": __version__,
            "transport": "streamable-http",
        }
    )


def create_app(
    *,
    path: str = DEFAULT_PATH,
    token: str | None = None,
    allowed_hosts: list[str] | None = None,
    stateless_http: bool = False,
    host: str = "127.0.0.1",
):
    """Build the HTTP app around the exact same MCPServer used by stdio."""
    transport_security = None
    if allowed_hosts:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[
                origin
                for host_value in allowed_hosts
                for origin in (
                    f"https://{host_value}",
                    f"http://{host_value}",
                )
            ],
        )
    app = mcp.streamable_http_app(
        streamable_http_path=path,
        stateless_http=stateless_http,
        host=host,
        transport_security=transport_security,
        max_request_body_size=4 * 1024 * 1024,
    )
    # Add /healthz without replacing the MCP app's lifespan/session manager.
    app.router.routes.insert(0, Route("/healthz", healthz, methods=["GET"]))
    if token:
        app.add_middleware(BearerTokenMiddleware, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "mcp-session-id"],
    )
    return app


def _split_hosts(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="News MCP remote Streamable HTTP server")
    parser.add_argument("--host", default=os.environ.get("NEWS_MCP_HTTP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("NEWS_MCP_HTTP_PORT", DEFAULT_PORT))
    )
    parser.add_argument("--path", default=os.environ.get("NEWS_MCP_HTTP_PATH", DEFAULT_PATH))
    parser.add_argument("--token", default=os.environ.get("NEWS_MCP_HTTP_TOKEN"))
    parser.add_argument(
        "--allowed-hosts",
        default=os.environ.get("NEWS_MCP_ALLOWED_HOSTS", ""),
        help="逗号分隔的 Host 白名单，例如 news.example.com,127.0.0.1:8765",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=os.environ.get("NEWS_MCP_HTTP_STATELESS", "").lower() in {"1", "true", "yes"},
        help="使用无状态 Streamable HTTP（多实例部署时建议开启）",
    )
    parser.add_argument(
        "--log-level", default=os.environ.get("NEWS_MCP_LOG_LEVEL", "INFO")
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(
        path=args.path,
        token=args.token,
        allowed_hosts=_split_hosts(args.allowed_hosts),
        stateless_http=args.stateless,
        host=args.host,
    )
    log.info(
        "serving MCP Streamable HTTP on http://%s:%s%s (token=%s, stateless=%s)",
        args.host,
        args.port,
        args.path,
        "on" if args.token else "off",
        args.stateless,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=str(args.log_level).lower())


if __name__ == "__main__":
    main()
