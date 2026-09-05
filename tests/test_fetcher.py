import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import aiohttp

from src.fetcher import (
    ArticleCandidate,
    _clean_summary,
    _parse_entry_date,
    _parse_feed,
    _fetch_one,
    fetch_all,
    TIME_WINDOW_HOURS,
)


RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Article Récent</title>
      <link>https://example.com/recent</link>
      <summary>&lt;p&gt;Résumé &lt;b&gt;test&lt;/b&gt; avec du HTML&lt;/p&gt;</summary>
      <pubDate>{recent_date}</pubDate>
    </item>
    <item>
      <title>Article Ancien</title>
      <link>https://example.com/old</link>
      <summary>Un vieux résumé</summary>
      <pubDate>{old_date}</pubDate>
    </item>
    <item>
      <title>Sans Date</title>
      <link>https://example.com/nodate</link>
      <summary>Pas de date ici</summary>
    </item>
  </channel>
</rss>"""


def _rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _make_source(name="Test", url="https://example.com/feed"):
    return {"name": name, "url": url, "category": "lab"}


class TestCleanSummary:
    def test_strips_html(self):
        assert _clean_summary("<p>Hello <b>world</b></p>") == "Hello world"

    def test_truncates(self):
        raw = "x" * 300
        assert len(_clean_summary(raw)) == 250

    def test_empty(self):
        assert _clean_summary(None) == ""
        assert _clean_summary("") == ""


class TestParseEntryDate:
    def test_returns_pub_date(self):
        entry = MagicMock()
        entry.published_parsed = (2025, 1, 15, 10, 0, 0, 0, 0, 0)
        entry.updated_parsed = None
        dt = _parse_entry_date(entry)
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.tzinfo == timezone.utc

    def test_fallback_to_updated(self):
        entry = MagicMock()
        entry.published_parsed = None
        entry.updated_parsed = (2025, 6, 20, 12, 0, 0, 0, 0, 0)
        dt = _parse_entry_date(entry)
        assert dt.month == 6

    def test_returns_none_when_missing(self):
        entry = MagicMock()
        entry.published_parsed = None
        entry.updated_parsed = None
        assert _parse_entry_date(entry) is None


class TestParseFeed:
    def test_filters_old_and_missing_dates(self):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=TIME_WINDOW_HOURS)
        recent = _rfc822(now - timedelta(hours=2))
        old = _rfc822(now - timedelta(hours=48))
        xml = RSS_SAMPLE.format(recent_date=recent, old_date=old)
        articles = _parse_feed("TestSource", xml, cutoff)
        assert len(articles) == 1
        assert articles[0].title == "Article Récent"

    def test_cleans_html_in_summary(self):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=TIME_WINDOW_HOURS)
        recent = _rfc822(now - timedelta(hours=1))
        old = _rfc822(now - timedelta(hours=48))
        xml = RSS_SAMPLE.format(recent_date=recent, old_date=old)
        articles = _parse_feed("TestSource", xml, cutoff)
        assert "<p>" not in articles[0].summary
        assert "<b>" not in articles[0].summary


class TestFetchOne:
    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        result = await _fetch_one(session, _make_source(), datetime.now(timezone.utc))
        assert result == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_cm)

        result = await _fetch_one(session, _make_source(), datetime.now(timezone.utc))
        assert result == []

    @pytest.mark.asyncio
    async def test_valid_feed_returns_articles(self):
        now = datetime.now(timezone.utc)
        recent = _rfc822(now - timedelta(hours=1))
        old = _rfc822(now - timedelta(hours=48))
        xml = RSS_SAMPLE.format(recent_date=recent, old_date=old)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=xml)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        result = await _fetch_one(session, _make_source(), now - timedelta(hours=TIME_WINDOW_HOURS))
        assert len(result) == 1
        assert result[0].title == "Article Récent"
        assert result[0].source_name == "Test"


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_aggregates_multiple_sources(self):
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=TIME_WINDOW_HOURS)
        recent = _rfc822(now - timedelta(hours=1))
        old = _rfc822(now - timedelta(hours=48))
        xml = RSS_SAMPLE.format(recent_date=recent, old_date=old)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=xml)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        sources = [_make_source("S1"), _make_source("S2")]
        results = []
        for _ in sources:
            results.append([ArticleCandidate("S", "Art", "http://x", "sum", now)])

        with patch("src.fetcher.aiohttp.ClientSession") as mock_cls:
            session_instance = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=session_instance)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            session_instance.get = MagicMock(return_value=mock_resp)

            articles = await fetch_all(sources)
            assert len(articles) == 2
