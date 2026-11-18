"""Timezone-aware date parsing. All persisted instants are UTC ISO 8601."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
_DATE_ONLY = re.compile(r"^\s*(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*$")
_DATETIME_CN = re.compile(
    r"(?P<y>20\d{2})[-/年](?P<m>\d{1,2})[-/月](?P<d>\d{1,2})日?"
    r"(?:\s*(?:[T ]|-(?=\d{1,2}:))\s*(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?"
)


class DateParseError(ValueError):
    pass


@dataclass(slots=True)
class TimeInfo:
    published_at: str
    published_at_local: str
    published_date: str
    precision: str
    raw: str


def get_zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        # Windows ships no IANA tzdata; requirements.txt installs it. This
        # narrow fallback keeps the mandatory Asia/Shanghai source usable even
        # in a minimal environment (China has had no DST since 1991).
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8))
        raise DateParseError(f"未知时区: {name}") from exc


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise DateParseError("datetime 必须携带时区")
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_article_time(raw: str | None, timezone_name: str) -> TimeInfo:
    """Parse a date/time from a page.

    Accepts ISO 8601, Chinese date formats, date-only strings and RFC 2822.
    A date-only value is midnight local time and is explicitly marked
    ``date_only`` so callers can report reduced precision.
    """

    if not raw or not raw.strip():
        raise DateParseError("缺少发布时间")
    text = raw.strip()
    tz = get_zone(timezone_name)

    # ISO 8601 with timezone (or naive ISO treated as local).
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        has_time = bool(re.search(r"\d{1,2}:\d{2}", text))
        has_seconds = bool(re.search(r"\d{1,2}:\d{2}:\d{2}", text))
        precision = "second" if has_seconds else ("minute" if has_time else "date_only")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return _build(dt, precision, text)
    except ValueError:
        pass

    m = _DATETIME_CN.search(text)
    if m:
        parts = m.groupdict()
        hour = int(parts["H"]) if parts["H"] else 0
        minute = int(parts["M"]) if parts["M"] else 0
        second = int(parts["S"]) if parts["S"] else 0
        precision = "second" if parts["S"] else ("minute" if parts["H"] else "date_only")
        dt = datetime(
            int(parts["y"]),
            int(parts["m"]),
            int(parts["d"]),
            hour,
            minute,
            second,
            tzinfo=tz,
        )
        return _build(dt, precision, text)

    m = _DATE_ONLY.match(text)
    if m:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz)
        return _build(dt, "date_only", text)

    raise DateParseError(f"无法解析发布时间: {raw!r}")


def _build(dt: datetime, precision: str, raw: str) -> TimeInfo:
    local = dt
    utc = dt.astimezone(UTC)
    return TimeInfo(
        published_at=utc.isoformat(timespec="seconds"),
        published_at_local=local.isoformat(timespec="seconds"),
        published_date=local.date().isoformat(),
        precision=precision,
        raw=raw,
    )


def parse_search_date(value: str | None, field: str = "date") -> date | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        raise DateParseError(f"{field} 必须是 YYYY-MM-DD，收到 {value!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DateParseError(f"{field} 不是有效日期: {value!r}") from exc


def search_bounds(
    start_date: str | None,
    end_date: str | None,
    timezone_name: str,
) -> tuple[str | None, str | None]:
    """Return inclusive local-date filter as UTC [start, end) ISO strings."""

    start = parse_search_date(start_date, "start_date")
    end = parse_search_date(end_date, "end_date")
    if start and end and start > end:
        raise DateParseError("start_date 不能晚于 end_date")
    tz = get_zone(timezone_name)
    start_utc = None
    end_utc = None
    if start:
        start_utc = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC).isoformat(
            timespec="seconds"
        )
    if end:
        end_utc = (
            datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
            .astimezone(UTC)
            .isoformat(timespec="seconds")
        )
    return start_utc, end_utc


def parse_http_date(value: str | None, timezone_name: str) -> TimeInfo | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=get_zone(timezone_name))
        return _build(dt, "second", value)
    except (TypeError, ValueError):
        return None
