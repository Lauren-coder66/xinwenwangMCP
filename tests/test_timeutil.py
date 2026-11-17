from __future__ import annotations

import pytest

from news_mcp.timeutil import DateParseError, parse_article_time, search_bounds


def test_parse_xinhua_timestamp_to_utc_and_local():
    info = parse_article_time("来源：新华社 2026-11-17 14:41:40", "Asia/Shanghai")
    assert info.published_at == "2026-11-17T06:41:40+00:00"
    assert info.published_at_local == "2026-11-17T14:41:40+08:00"
    assert info.published_date == "2026-11-17"
    assert info.precision == "second"


def test_parse_chinanews_chinese_timestamp():
    info = parse_article_time("2026年11月17日 21:37　来源：", "Asia/Shanghai")
    assert info.published_at_local == "2026-11-17T21:37:00+08:00"
    assert info.precision == "minute"


def test_parse_date_only_marks_reduced_precision():
    info = parse_article_time("2026-11-17", "Asia/Shanghai")
    assert info.published_at_local == "2026-11-17T00:00:00+08:00"
    assert info.precision == "date_only"


def test_parse_invalid_raises():
    with pytest.raises(DateParseError):
        parse_article_time("not-a-date", "Asia/Shanghai")


def test_search_bounds_are_local_midnight_to_exclusive_next_day():
    start, end = search_bounds("2026-11-17", "2026-11-17", "Asia/Shanghai")
    assert start == "2026-11-16T16:00:00+00:00"
    assert end == "2026-11-17T16:00:00+00:00"


def test_search_bounds_reject_reversed_range():
    with pytest.raises(DateParseError):
        search_bounds("2026-11-18", "2026-11-17", "Asia/Shanghai")
