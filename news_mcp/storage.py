"""SQLite persistence: multi-site articles, validation state, refresh audit."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import SiteConfig
from .models import Candidate, ParsedArticle
from .timeutil import now_utc

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    site_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    homepage TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unvalidated',
    validated_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    site_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    validated INTEGER NOT NULL DEFAULT 0,
    validation_status TEXT NOT NULL DEFAULT 'unvalidated',
    last_checked_at TEXT,
    last_refresh_at TEXT,
    last_error TEXT,
    PRIMARY KEY (site_id, channel_id),
    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    original_url TEXT,
    final_url TEXT,
    published_at TEXT,
    published_at_local TEXT,
    published_date TEXT,
    date_precision TEXT,
    timezone TEXT NOT NULL,
    content TEXT,
    summary TEXT,
    author TEXT,
    source TEXT,
    fetched_at TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    extraction_error TEXT,
    http_status INTEGER,
    content_hash TEXT,
    content_chars INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (site_id, url),
    FOREIGN KEY (site_id) REFERENCES sites(site_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_articles_site_date
    ON articles(site_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_channel_date
    ON articles(site_id, channel_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_published_date
    ON articles(published_date);

CREATE TABLE IF NOT EXISTS refresh_runs (
    run_id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    requested_limit INTEGER NOT NULL,
    attempted_count INTEGER NOT NULL DEFAULT 0,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    partial_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    source_mode TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    errors_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_runs_site_time
    ON refresh_runs(site_id, started_at DESC);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StorageError(RuntimeError):
    pass


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        con = self.connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = self.connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    def initialize(self) -> None:
        with self.transaction() as con:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # ------------------------------------------------------------------ sites
    def seed_sites(self, sites: list[SiteConfig]) -> list[dict[str, Any]]:
        now = now_utc()
        changes: list[dict[str, Any]] = []
        with self.transaction() as con:
            for site in sites:
                old = con.execute(
                    "SELECT config_hash FROM sites WHERE site_id=?", (site.site_id,)
                ).fetchone()
                hash_changed = bool(old and old["config_hash"] != site.config_hash)
                if old is None:
                    con.execute(
                        """INSERT INTO sites
                        (site_id,name,homepage,enabled,config_hash,status,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            site.site_id,
                            site.name,
                            site.homepage,
                            1 if site.enabled else 0,
                            site.config_hash,
                            "unvalidated",
                            now,
                            now,
                        ),
                    )
                    changes.append({"site_id": site.site_id, "action": "inserted"})
                else:
                    con.execute(
                        """UPDATE sites SET name=?, homepage=?, enabled=?, config_hash=?,
                        updated_at=? WHERE site_id=?""",
                        (
                            site.name,
                            site.homepage,
                            1 if site.enabled else 0,
                            site.config_hash,
                            now,
                            site.site_id,
                        ),
                    )
                    if hash_changed:
                        con.execute(
                            """UPDATE sites SET status='unvalidated', last_error=?,
                            updated_at=? WHERE site_id=?""",
                            (
                                "配置已更改，需要重新运行 validate_site",
                                now,
                                site.site_id,
                            ),
                        )
                        con.execute(
                            """UPDATE channels SET validated=0,
                            validation_status='unvalidated',
                            last_error=? WHERE site_id=?""",
                            ("配置已更改，需要重新验证", site.site_id),
                        )
                    changes.append(
                        {
                            "site_id": site.site_id,
                            "action": "updated" if not hash_changed else "config_changed",
                        }
                    )
                existing_channels = {
                    row["channel_id"]
                    for row in con.execute(
                        "SELECT channel_id FROM channels WHERE site_id=?", (site.site_id,)
                    )
                }
                for channel in site.channels:
                    if channel.id in existing_channels:
                        con.execute(
                            """UPDATE channels SET name=?, url=?, enabled=?
                            WHERE site_id=? AND channel_id=?""",
                            (
                                channel.name,
                                channel.url,
                                1 if channel.enabled else 0,
                                site.site_id,
                                channel.id,
                            ),
                        )
                    else:
                        con.execute(
                            """INSERT INTO channels
                            (site_id,channel_id,name,url,enabled,validated,
                             validation_status)
                            VALUES (?,?,?,?,?,0,'unvalidated')""",
                            (
                                site.site_id,
                                channel.id,
                                channel.name,
                                channel.url,
                                1 if channel.enabled else 0,
                            ),
                        )
                configured = {c.id for c in site.channels}
                for removed in existing_channels - configured:
                    con.execute(
                        "UPDATE channels SET enabled=0 WHERE site_id=? AND channel_id=?",
                        (site.site_id, removed),
                    )
        return changes

    def update_validation(self, site_id: str, report: dict[str, Any]) -> None:
        now = now_utc()
        reports = {item["channel_id"]: item for item in report.get("channels", [])}
        with self.transaction() as con:
            for channel_id, item in reports.items():
                con.execute(
                    """UPDATE channels SET validated=?, validation_status=?,
                    last_checked_at=?, last_error=? WHERE site_id=? AND channel_id=?""",
                    (
                        1 if item.get("ok") else 0,
                        "valid" if item.get("ok") else "invalid",
                        report.get("validated_at") or now,
                        "; ".join(e.get("message", "") for e in item.get("errors", []))
                        or None,
                        site_id,
                        channel_id,
                    ),
                )
            any_valid = any(item.get("ok") for item in reports.values())
            con.execute(
                """UPDATE sites SET status=?, validated_at=?, last_error=?,
                updated_at=? WHERE site_id=?""",
                (
                    "active" if any_valid else "validation_failed",
                    report.get("validated_at") or now,
                    None if any_valid else "没有频道通过真实验证",
                    now,
                    site_id,
                ),
            )

    def get_site(self, site_id: str) -> dict[str, Any] | None:
        with self.read_connection() as con:
            row = con.execute("SELECT * FROM sites WHERE site_id=?", (site_id,)).fetchone()
            return dict(row) if row else None

    def list_sites(self) -> list[dict[str, Any]]:
        with self.read_connection() as con:
            rows = con.execute(
                """SELECT s.*,
                (SELECT COUNT(*) FROM channels c WHERE c.site_id=s.site_id AND c.enabled=1) AS channel_count,
                (SELECT COUNT(*) FROM channels c WHERE c.site_id=s.site_id AND c.enabled=1 AND c.validated=1) AS valid_channel_count,
                (SELECT COUNT(*) FROM articles a WHERE a.site_id=s.site_id) AS article_count,
                (SELECT MAX(finished_at) FROM refresh_runs r WHERE r.site_id=s.site_id) AS last_refresh_at
                FROM sites s ORDER BY s.site_id"""
            ).fetchall()
            return [dict(row) for row in rows]

    # --------------------------------------------------------------- channels
    def list_channels(self, site_id: str | None = None, valid_only: bool = True) -> list[dict[str, Any]]:
        sql = """
            SELECT c.*, s.name AS site_name,
            (SELECT COUNT(*) FROM articles a
             WHERE a.site_id=c.site_id AND a.channel_id=c.channel_id) AS article_count
            FROM channels c JOIN sites s ON s.site_id=c.site_id
            WHERE c.enabled=1 AND s.enabled=1
        """
        params: list[Any] = []
        if site_id:
            sql += " AND c.site_id=?"
            params.append(site_id)
        if valid_only:
            sql += " AND c.validated=1"
        sql += " ORDER BY c.site_id, c.channel_id"
        with self.read_connection() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def get_channel(self, site_id: str, channel_id: str) -> dict[str, Any] | None:
        with self.read_connection() as con:
            row = con.execute(
                "SELECT * FROM channels WHERE site_id=? AND channel_id=?",
                (site_id, channel_id),
            ).fetchone()
            return dict(row) if row else None

    def mark_channel_refresh(
        self, site_id: str, channel_id: str, status: str, error: str | None
    ) -> None:
        with self.transaction() as con:
            con.execute(
                """UPDATE channels SET last_refresh_at=?, last_error=?
                WHERE site_id=? AND channel_id=?""",
                (now_utc(), error, site_id, channel_id),
            )

    # --------------------------------------------------------------- articles
    def upsert_article(
        self,
        site: SiteConfig,
        channel: dict[str, Any],
        parsed: ParsedArticle,
        candidate: Candidate | None = None,
    ) -> str:
        """Insert or update one article. Returns inserted/updated/unchanged."""

        if not parsed.title:
            return "unchanged"
        now = now_utc()
        source = parsed.source or site.name
        original_url = candidate.url if candidate else parsed.url
        content = parsed.content or ""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        content_chars = len("".join(content.split()))
        with self.transaction() as con:
            existing = con.execute(
                "SELECT * FROM articles WHERE site_id=? AND url=?",
                (site.site_id, parsed.url),
            ).fetchone()
            if existing is None:
                con.execute(
                    """INSERT INTO articles
                    (site_id,channel_id,channel_name,title,url,original_url,final_url,
                     published_at,published_at_local,published_date,date_precision,timezone,
                     content,summary,author,source,fetched_at,extraction_status,
                     extraction_error,http_status,content_hash,content_chars,
                     first_seen_at,last_seen_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        site.site_id,
                        channel["channel_id"],
                        channel["name"],
                        parsed.title,
                        parsed.url,
                        original_url,
                        parsed.final_url,
                        parsed.published_at,
                        parsed.published_at_local,
                        parsed.published_date,
                        parsed.date_precision,
                        parsed.timezone,
                        content or None,
                        parsed.summary,
                        parsed.author,
                        source,
                        parsed.fetched_at,
                        parsed.extraction_status,
                        parsed.extraction_error,
                        parsed.http_status,
                        content_hash,
                        content_chars,
                        now,
                        now,
                        now,
                    ),
                )
                return "inserted"

            old_status = existing["extraction_status"]
            # Never replace a previously good body with a failed/partial retry.
            keep_good = old_status == "ok" and parsed.extraction_status != "ok"
            fields = {
                "channel_id": channel["channel_id"],
                "channel_name": channel["name"],
                "title": parsed.title,
                "original_url": original_url,
                "final_url": parsed.final_url,
                "published_at": parsed.published_at or existing["published_at"],
                "published_at_local": parsed.published_at_local or existing["published_at_local"],
                "published_date": parsed.published_date or existing["published_date"],
                "date_precision": parsed.date_precision or existing["date_precision"],
                "content": content if content else existing["content"],
                "summary": parsed.summary or existing["summary"],
                "author": parsed.author or existing["author"],
                "source": source or existing["source"],
                "fetched_at": parsed.fetched_at,
                "extraction_status": old_status if keep_good else parsed.extraction_status,
                "extraction_error": existing["extraction_error"] if keep_good else parsed.extraction_error,
                "http_status": parsed.http_status,
                "content_hash": content_hash if content else existing["content_hash"],
                "content_chars": content_chars if content else existing["content_chars"],
            }
            if keep_good:
                fields["content"] = existing["content"]
                fields["content_hash"] = existing["content_hash"]
                fields["content_chars"] = existing["content_chars"]
            changed = any(
                str(fields[key]) != str(existing[key])
                for key in (
                    "title",
                    "published_at",
                    "content",
                    "source",
                    "channel_id",
                    "extraction_status",
                )
            )
            con.execute(
                """UPDATE articles SET channel_id=?, channel_name=?, title=?,
                original_url=?, final_url=?, published_at=?, published_at_local=?,
                published_date=?, date_precision=?, content=?, summary=?, author=?,
                source=?, fetched_at=?, extraction_status=?, extraction_error=?,
                http_status=?, content_hash=?, content_chars=?, last_seen_at=?, updated_at=?
                WHERE id=?""",
                (
                    fields["channel_id"],
                    fields["channel_name"],
                    fields["title"],
                    fields["original_url"],
                    fields["final_url"],
                    fields["published_at"],
                    fields["published_at_local"],
                    fields["published_date"],
                    fields["date_precision"],
                    fields["content"],
                    fields["summary"],
                    fields["author"],
                    fields["source"],
                    fields["fetched_at"],
                    fields["extraction_status"],
                    fields["extraction_error"],
                    fields["http_status"],
                    fields["content_hash"],
                    fields["content_chars"],
                    now,
                    now,
                    existing["id"],
                ),
            )
            return "updated" if changed else "unchanged"

    def get_article(self, site_id: str, url: str) -> dict[str, Any] | None:
        with self.read_connection() as con:
            row = con.execute(
                """SELECT a.*, s.name AS site_name FROM articles a
                JOIN sites s ON s.site_id=a.site_id
                WHERE a.site_id=? AND a.url=?""",
                (site_id, url),
            ).fetchone()
            return dict(row) if row else None

    def list_latest(
        self,
        *,
        site_id: str | None = None,
        channel_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        sql = """SELECT a.*, s.name AS site_name FROM articles a
                 JOIN sites s ON s.site_id=a.site_id WHERE 1=1"""
        params: list[Any] = []
        if site_id:
            sql += " AND a.site_id=?"
            params.append(site_id)
        if channel_id:
            sql += " AND a.channel_id=?"
            params.append(channel_id)
        sql += " ORDER BY (a.published_at IS NULL), a.published_at DESC, a.id DESC LIMIT ?"
        params.append(limit)
        with self.read_connection() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def search(
        self,
        *,
        keyword: str,
        start_utc: str | None,
        end_utc: str | None,
        site_id: str | None,
        channel_id: str | None,
        limit: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        where = ["1=1"]
        params: list[Any] = []
        tokens = [t for t in keyword.split() if t]
        for token in tokens:
            escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                "(a.title LIKE ? ESCAPE '\\' OR a.content LIKE ? ESCAPE '\\' OR a.summary LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{escaped}%", f"%{escaped}%", f"%{escaped}%"])
        if start_utc:
            where.append("a.published_at >= ?")
            params.append(start_utc)
        if end_utc:
            where.append("a.published_at < ?")
            params.append(end_utc)
        if site_id:
            where.append("a.site_id = ?")
            params.append(site_id)
        if channel_id:
            where.append("a.channel_id = ?")
            params.append(channel_id)
        clause = " AND ".join(where)
        with self.read_connection() as con:
            total = con.execute(
                f"SELECT COUNT(*) FROM articles a WHERE {clause}", params
            ).fetchone()[0]
            rows = con.execute(
                f"""SELECT a.*, s.name AS site_name FROM articles a
                JOIN sites s ON s.site_id=a.site_id
                WHERE {clause}
                ORDER BY (a.published_at IS NULL), a.published_at DESC, a.id DESC
                LIMIT ?""",
                [*params, limit],
            ).fetchall()
            return int(total), [dict(row) for row in rows]

    def count_articles(self, site_id: str | None = None) -> int:
        with self.read_connection() as con:
            if site_id:
                return int(
                    con.execute(
                        "SELECT COUNT(*) FROM articles WHERE site_id=?", (site_id,)
                    ).fetchone()[0]
                )
            return int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    # ------------------------------------------------------------- refresh runs
    def start_refresh_run(
        self, site_id: str, channel_id: str, requested_limit: int
    ) -> str:
        run_id = uuid.uuid4().hex
        with self.transaction() as con:
            con.execute(
                """INSERT INTO refresh_runs
                (run_id,site_id,channel_id,requested_limit,status,started_at)
                VALUES (?,?,?,?,'running',?)""",
                (run_id, site_id, channel_id, requested_limit, now_utc()),
            )
        return run_id

    def finish_refresh_run(self, run_id: str, result: dict[str, Any]) -> None:
        with self.transaction() as con:
            con.execute(
                """UPDATE refresh_runs SET attempted_count=?, discovered_count=?,
                inserted_count=?, updated_count=?, failed_count=?, partial_count=?,
                status=?, source_mode=?, finished_at=?, errors_json=?
                WHERE run_id=?""",
                (
                    result.get("attempted_count", 0),
                    result.get("discovered_count", 0),
                    result.get("inserted_count", 0),
                    result.get("updated_count", 0),
                    result.get("failed_count", 0),
                    result.get("partial_count", 0),
                    result.get("status", "error"),
                    result.get("source_mode"),
                    result.get("fetched_at") or now_utc(),
                    json.dumps(result.get("errors", []), ensure_ascii=False),
                    run_id,
                ),
            )

    def recent_runs(self, site_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM refresh_runs"
        params: list[Any] = []
        if site_id:
            sql += " WHERE site_id=?"
            params.append(site_id)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self.read_connection() as con:
            rows = [dict(row) for row in con.execute(sql, params).fetchall()]
            for row in rows:
                row["errors"] = json.loads(row.pop("errors_json") or "[]")
            return rows
