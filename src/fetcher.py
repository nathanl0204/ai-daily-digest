import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SOURCE_TIMEOUT = 5
TIME_WINDOW_HOURS = 26
MAX_SUMMARY_LENGTH = 250


@dataclass
class ArticleCandidate:
    source_name: str
    title: str
    link: str
    summary: str
    published_at: datetime


def _clean_summary(raw: str | None) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text()
    return text[:MAX_SUMMARY_LENGTH]


def _parse_entry_date(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return None


def _parse_feed(source_name: str, raw_xml: str, cutoff: datetime) -> list[ArticleCandidate]:
    feed = feedparser.parse(raw_xml)
    articles: list[ArticleCandidate] = []
    for entry in feed.entries:
        pub_date = _parse_entry_date(entry)
        if pub_date is None:
            continue
        if pub_date < cutoff:
            continue
        articles.append(
            ArticleCandidate(
                source_name=source_name,
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                summary=_clean_summary(entry.get("summary")),
                published_at=pub_date,
            )
        )
    return articles


async def _fetch_one(
    session: aiohttp.ClientSession,
    source: dict,
    cutoff: datetime,
) -> list[ArticleCandidate]:
    name = source["name"]
    url = source["url"]
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=SOURCE_TIMEOUT)) as resp:
            if resp.status >= 400:
                logger.warning("HTTP %d pour %s (%s) — ignoré", resp.status, name, url)
                return []
            raw_xml = await resp.text(errors="replace")
    except asyncio.TimeoutError:
        logger.warning("Timeout pour %s (%s) — ignoré", name, url)
        return []
    except aiohttp.ClientError as exc:
        logger.warning("Erreur réseau pour %s (%s): %s — ignoré", name, url, exc)
        return []

    try:
        return _parse_feed(name, raw_xml, cutoff)
    except Exception as exc:
        logger.warning("Erreur parsing XML pour %s: %s — ignoré", name, exc)
        return []


async def fetch_all(sources: list[dict]) -> list[ArticleCandidate]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_one(session, src, cutoff) for src in sources]
        results = await asyncio.gather(*tasks)
    articles = [a for batch in results for a in batch]
    articles.sort(key=lambda a: a.published_at, reverse=True)
    return articles
