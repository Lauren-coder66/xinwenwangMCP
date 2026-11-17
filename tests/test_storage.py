from __future__ import annotations

from dataclasses import replace

from news_mcp.storage import Database


def _channel(db: Database, site_id: str = "xinhua") -> dict:
    return db.get_channel(site_id, "politics")


def test_insert_deduplicate_and_update_content(tmp_path, xinhua_site, make_parsed):
    db = Database(tmp_path / "news.db")
    db.seed_sites([xinhua_site])
    channel = _channel(db)
    article = make_parsed()

    assert db.upsert_article(xinhua_site, channel, article) == "inserted"
    assert db.count_articles() == 1
    # Same URL, same values: no duplicate, no logical update.
    assert db.upsert_article(xinhua_site, channel, article) == "unchanged"
    assert db.count_articles() == 1
    # Changed body: updated in place.
    changed = replace(article, content="更新后的正文。" * 20)
    assert db.upsert_article(xinhua_site, channel, changed) == "updated"
    assert db.count_articles() == 1
    assert "更新后的正文" in db.get_article("xinhua", article.url)["content"]


def test_failed_retry_does_not_overwrite_good_body(tmp_path, xinhua_site, make_parsed):
    db = Database(tmp_path / "news.db")
    db.seed_sites([xinhua_site])
    channel = _channel(db)
    good = make_parsed(content="已经成功提取的正文。" * 20)
    db.upsert_article(xinhua_site, channel, good)
    degraded = make_parsed(
        content="短", status="partial", error="正文过短"
    )
    assert db.upsert_article(xinhua_site, channel, degraded) == "unchanged"
    stored = db.get_article("xinhua", good.url)
    assert "已经成功提取" in stored["content"]
    assert stored["extraction_status"] == "ok"


def test_search_filters_keyword_channel_site_and_inclusive_dates(
    tmp_path, xinhua_site, make_parsed
):
    db = Database(tmp_path / "news.db")
    db.seed_sites([xinhua_site])
    channel = _channel(db)
    db.upsert_article(
        xinhua_site,
        channel,
        make_parsed(
            title="医保改革新闻",
            content="医保 wallet 测试内容。" * 10,
            published_at="2026-11-17T03:00:00+00:00",
            published_at_local="2026-11-17T11:00:00+08:00",
        ),
    )
    db.upsert_article(
        xinhua_site,
        channel,
        make_parsed(
            url="https://www.news.cn/politics/20261116/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/c.html",
            title="文化新闻",
            content="文化活动测试内容。" * 10,
            published_at="2026-11-16T03:00:00+00:00",
            published_at_local="2026-11-16T11:00:00+08:00",
        ),
    )
    total, rows = db.search(
        keyword="医保",
        start_utc="2026-11-16T16:00:00+00:00",
        end_utc="2026-11-17T16:00:00+00:00",
        site_id="xinhua",
        channel_id="politics",
        limit=10,
    )
    assert total == 1
    assert rows[0]["title"] == "医保改革新闻"
    total2, _ = db.search(
        keyword="不存在",
        start_utc=None,
        end_utc=None,
        site_id="xinhua",
        channel_id=None,
        limit=10,
    )
    assert total2 == 0


def test_config_change_invalidates_validation_but_keeps_articles(
    tmp_path, xinhua_site, make_parsed
):
    db = Database(tmp_path / "news.db")
    db.seed_sites([xinhua_site])
    db.upsert_article(xinhua_site, _channel(db), make_parsed())
    db.update_validation(
        "xinhua",
        {
            "validated_at": "2026-11-17T13:00:00+00:00",
            "channels": [{"channel_id": "politics", "ok": True, "errors": []}],
        },
    )
    assert db.get_channel("xinhua", "politics")["validated"] == 1

    changed = xinhua_site.model_copy(update={"name": "新华网（新配置）"})
    db.seed_sites([changed])
    assert db.get_channel("xinhua", "politics")["validated"] == 0
    assert db.count_articles() == 1  # data is never deleted by reconfiguration
