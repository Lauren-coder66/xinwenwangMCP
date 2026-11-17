from __future__ import annotations

import yaml

from news_mcp.config import load_registry


def _site(site_id: str) -> dict:
    return {
        "site_id": site_id,
        "name": f"Site {site_id}",
        "homepage": "https://example.com/",
        "enabled": True,
        "allowed_domains": ["example.com"],
        "article_url_patterns": [r"^https://example\.com/\d+\.html$"],
        "channels": [
            {
                "id": "main",
                "name": "Main",
                "url": "https://example.com/",
                "discovery": [
                    {
                        "type": "html",
                        "url": "https://example.com/",
                        "url_pattern": r"^/\d+\.html$",
                    }
                ],
            }
        ],
    }


def test_one_bad_site_does_not_break_others(tmp_path):
    sites = tmp_path / "sites"
    sites.mkdir()
    (tmp_path / "settings.yaml").write_text("default_site: good\n", encoding="utf-8")
    (sites / "good.yaml").write_text(
        yaml.safe_dump(_site("good"), allow_unicode=True), encoding="utf-8"
    )
    (sites / "bad.yaml").write_text("site_id: bad\nchannels: [", encoding="utf-8")
    registry = load_registry(tmp_path)
    assert "good" in registry.sites
    assert "bad" not in registry.sites
    assert len(registry.errors) == 1
    assert "bad.yaml" in registry.errors[0]["file"]


def test_duplicate_site_id_is_isolated(tmp_path):
    sites = tmp_path / "sites"
    sites.mkdir()
    (tmp_path / "settings.yaml").write_text("default_site: same\n", encoding="utf-8")
    (sites / "a.yaml").write_text(
        yaml.safe_dump(_site("same"), allow_unicode=True), encoding="utf-8"
    )
    data = _site("same")
    (sites / "b.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    registry = load_registry(tmp_path)
    assert len(registry.sites) == 1
    assert len(registry.errors) == 1


def test_unknown_fields_are_rejected_for_typo_safety(tmp_path):
    sites = tmp_path / "sites"
    sites.mkdir()
    data = _site("typo")
    data["request_delay_second"] = 1.5  # typo: should fail, not silently ignored
    (sites / "typo.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    registry = load_registry(tmp_path)
    assert "typo" not in registry.sites
    assert registry.errors
