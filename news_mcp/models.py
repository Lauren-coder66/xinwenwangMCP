"""Stable internal data structures shared by adapters, storage and MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    """An article link discovered on a channel page / feed / sitemap."""

    url: str
    title: str
    channel_id: str
    source_mode: str
    published_at: str | None = None
    published_at_raw: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FetchResult:
    """Result of one HTTP request, including redirect and error details."""

    url: str
    final_url: str | None = None
    status_code: int | None = None
    text: str | None = None
    content_type: str | None = None
    elapsed_ms: int = 0
    error_code: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error_code is None
            and self.status_code == 200
            and self.text is not None
        )


@dataclass(slots=True)
class ParsedArticle:
    """Structured article. ``extraction_status`` is ok/partial/failed."""

    title: str | None
    url: str
    published_at: str | None
    published_at_local: str | None
    published_date: str | None
    date_precision: str | None
    timezone: str
    content: str | None
    summary: str | None
    author: str | None
    source: str | None
    fetched_at: str
    extraction_status: str
    extraction_error: str | None
    http_status: int | None = None
    final_url: str | None = None
    raw_date: str | None = None

    @property
    def ok(self) -> bool:
        return self.extraction_status == "ok"


@dataclass(slots=True)
class DiscoveryResult:
    """Articles discovered from one source, plus non-fatal source errors."""

    candidates: list[Candidate]
    source_mode: str
    errors: list[dict[str, str]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationCheck:
    name: str
    ok: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "evidence": self.evidence,
        }
