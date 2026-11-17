"""Command-line interface for setup, validation, collection and diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .service import NewsService


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _service(args) -> NewsService:
    return NewsService.create(args.config_dir, args.db)


def cmd_init(args) -> int:
    svc = _service(args)
    try:
        _print(svc.list_sites())
        return 0 if not svc.registry.errors else 1
    finally:
        svc.close()


def cmd_sites(args) -> int:
    svc = _service(args)
    try:
        _print(svc.list_sites())
        return 0
    finally:
        svc.close()


def cmd_channels(args) -> int:
    svc = _service(args)
    try:
        result = svc.list_channels(args.site)
        _print(result)
        return 0 if result.get("ok") else 1
    finally:
        svc.close()


def cmd_validate(args) -> int:
    svc = _service(args)
    try:
        targets = [s.site_id for s in svc.registry.sites.values()] if args.all else [args.site]
        if not args.all and not args.site:
            print("必须指定 --site 或 --all", file=sys.stderr)
            return 2
        results = []
        for site_id in targets:
            result = svc.validate_site(site_id)
            results.append(result)
            _print(result)
        return 0 if all(r.get("ok") for r in results) else 1
    finally:
        svc.close()


def cmd_refresh(args) -> int:
    svc = _service(args)
    try:
        result = svc.refresh_news(args.channel, args.limit, site_id=args.site)
        _print(result)
        return 0 if result.get("status") in {"ok", "partial"} else 1
    finally:
        svc.close()


def cmd_latest(args) -> int:
    svc = _service(args)
    try:
        result = svc.get_latest_news(
            args.channel, args.limit, refresh=args.refresh, site_id=args.site
        )
        _print(result)
        return 0 if result.get("ok") else 1
    finally:
        svc.close()


def cmd_search(args) -> int:
    svc = _service(args)
    try:
        result = svc.search_news(
            args.keyword,
            start_date=args.start,
            end_date=args.end,
            channel=args.channel,
            limit=args.limit,
            site_id=args.site,
        )
        _print(result)
        return 0 if result.get("ok") else 1
    finally:
        svc.close()


def cmd_article(args) -> int:
    svc = _service(args)
    try:
        result = svc.get_article(args.url, site_id=args.site)
        _print(result)
        return 0 if result.get("ok") else 1
    finally:
        svc.close()


def cmd_inspect(args) -> int:
    svc = _service(args)
    try:
        result = svc.inspect_site(args.url)
        _print(result)
        return 0 if result.get("ok") else 1
    finally:
        svc.close()


def cmd_serve(args) -> int:
    if args.config_dir:
        os.environ.setdefault("NEWS_MCP_CONFIG_DIR", str(Path(args.config_dir).resolve()))
    if args.db:
        os.environ["NEWS_MCP_DB"] = str(Path(args.db).resolve())
    if args.log_level:
        os.environ["NEWS_MCP_LOG_LEVEL"] = args.log_level
    from .server import main as server_main

    server_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="news-mcp-cli",
        description="Multi-site news MCP server CLI (real collection only)",
    )
    parser.add_argument("--config-dir", default=None, help="YAML 配置目录")
    parser.add_argument("--db", default=None, help="SQLite 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化/同步配置到 SQLite")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("sites", help="列出站点")
    p.set_defaults(func=cmd_sites)

    p = sub.add_parser("channels", help="列出已验证频道")
    p.add_argument("--site", default=None)
    p.set_defaults(func=cmd_channels)

    p = sub.add_parser("validate", help="真实验证站点")
    p.add_argument("--site", default=None)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("refresh", help="真实采集一个频道")
    p.add_argument("--site", required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("latest", help="查询最新新闻")
    p.add_argument("--channel", required=True)
    p.add_argument("--site", default=None)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_latest)

    p = sub.add_parser("search", help="检索本地已采集文章")
    p.add_argument("--keyword", required=True)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--channel", default=None)
    p.add_argument("--site", default=None)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("article", help="实时读取一篇文章")
    p.add_argument("--url", required=True)
    p.add_argument("--site", default=None)
    p.set_defaults(func=cmd_article)

    p = sub.add_parser("inspect", help="检测网站并生成候选配置")
    p.add_argument("--url", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("serve", help="启动 MCP stdio 服务")
    p.add_argument("--log-level", default="INFO")
    p.set_defaults(func=cmd_serve)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
