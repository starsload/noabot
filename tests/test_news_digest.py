import json
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.news_digest import (
    FeedSource,
    NewsDigestConfig,
    NewsDigestRunResult,
    NewsItem,
    load_news_digest_config,
    load_state,
    parse_feed,
    save_state,
    select_news_items,
)


runner = CliRunner()


def test_parse_feed_extracts_rss_items_and_normalizes_tracking_links():
    feed_text = """
    <rss version="2.0">
      <channel>
        <item>
          <title>Hello &amp; World</title>
          <link>https://example.com/post?utm_source=test&amp;foo=bar</link>
          <description><![CDATA[<p>Intro <b>bold</b> text</p>]]></description>
          <pubDate>Tue, 25 Mar 2026 09:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    items = parse_feed(feed_text, FeedSource(name="Example", url="https://example.com/rss.xml"))

    assert len(items) == 1
    item = items[0]
    assert item.title == "Hello & World"
    assert item.summary == "Intro bold text"
    assert item.link == "https://example.com/post?utm_source=test&foo=bar"
    assert item.key == "https://example.com/post?foo=bar"
    assert item.published_at == datetime(2026, 3, 25, 9, 0, tzinfo=timezone.utc)


def test_parse_feed_supports_atom_entries():
    feed_text = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Atom Headline</title>
        <link href="https://example.com/atom-entry" rel="alternate" />
        <summary type="html">&lt;p&gt;Atom summary&lt;/p&gt;</summary>
        <updated>2026-03-25T10:15:00Z</updated>
      </entry>
    </feed>
    """

    items = parse_feed(feed_text, FeedSource(name="Atom", url="https://example.com/atom.xml"))

    assert len(items) == 1
    item = items[0]
    assert item.title == "Atom Headline"
    assert item.link == "https://example.com/atom-entry"
    assert item.summary == "Atom summary"
    assert item.published_at == datetime(2026, 3, 25, 10, 15, tzinfo=timezone.utc)


def test_select_news_items_applies_window_seen_state_and_keywords():
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    config = NewsDigestConfig.model_validate(
        {
            "windowHours": 24,
            "maxItems": 3,
            "includeKeywords": ["ai"],
            "excludeKeywords": ["rumor"],
            "sources": [{"name": "Example", "url": "https://example.com/rss.xml"}],
        }
    )

    items = [
        NewsItem("Example", "AI Launch", "https://example.com/1", "Fresh update", now - timedelta(hours=1), "1"),
        NewsItem("Example", "Old AI", "https://example.com/2", "Still AI", now - timedelta(days=2), "2"),
        NewsItem("Example", "AI Rumor", "https://example.com/3", "rumor content", now - timedelta(hours=2), "3"),
        NewsItem("Example", "Another AI", "https://example.com/4", "Seen already", now - timedelta(minutes=30), "4"),
    ]

    selected = select_news_items(items, config, {"4": now - timedelta(hours=1)}, now=now)

    assert [item.key for item in selected] == ["1"]


def test_load_news_digest_config_expands_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_WEBHOOK_URL", "https://hooks.example.com/demo")
    config_path = tmp_path / "news.json"
    config_path.write_text(
        json.dumps(
            {
                "title": "Daily Demo",
                "sources": [{"name": "Example", "url": "https://example.com/rss.xml"}],
                "push": {
                    "type": "discord",
                    "webhookUrl": "${NEWS_WEBHOOK_URL}",
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_news_digest_config(config_path)

    assert config.title == "Daily Demo"
    assert config.push.webhook_url == "https://hooks.example.com/demo"


def test_save_state_prunes_expired_entries_and_round_trips(tmp_path):
    state_path = tmp_path / "news.state.json"
    now = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
    current_state = {
        "expired": now - timedelta(days=30),
        "recent": now - timedelta(days=1),
    }
    new_items = [
        NewsItem("Example", "Fresh", "https://example.com/fresh", "Summary", now, "fresh"),
    ]

    save_state(state_path, current_state, new_items, now=now, retention_days=14)
    loaded = load_state(state_path)

    assert "expired" not in loaded
    assert "recent" in loaded
    assert "fresh" in loaded


def test_news_digest_cli_runs_dry_run(monkeypatch, tmp_path):
    config_path = tmp_path / "news.json"
    config_path.write_text("{}", encoding="utf-8")
    seen = {}

    async def _fake_run(path, *, dry_run=False):
        seen["path"] = path
        seen["dry_run"] = dry_run
        return NewsDigestRunResult(
            content="# Demo",
            item_count=1,
            source_count=1,
            errors=[],
            sent_chunks=0,
            pushed=False,
            should_print=True,
            output_path=None,
            state_path=path.with_suffix(".state.json"),
        )

    monkeypatch.setattr("nanobot.news_digest.run_news_digest", _fake_run)

    result = runner.invoke(app, ["news-digest", "--config", str(config_path), "--dry-run"])

    assert result.exit_code == 0
    assert seen == {"path": config_path.resolve(), "dry_run": True}
    assert "Built digest with 1 items" in result.stdout
