"""RSS news aggregator — polls configured feeds hourly, normalizes items,
matches asset tickers/names, and persists linked articles."""
import asyncio
import calendar
import logging
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import feedparser
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import NewsArticle, NewsArticleMarket, RssFeed
from .market_data import market_data_service

logger = logging.getLogger("berebank.rss")

POLL_INTERVAL = 3600  # seconds

INITIAL_FEEDS = [
    ("https://www.coindesk.com/arc/outboundfeeds/rss", "CoinDesk"),
    ("https://cointelegraph.com/rss", "Cointelegraph"),
]

_STRIP_TAGS = re.compile(r"<[^>]*>")


def strip_html(html: str) -> str:
    text = unescape(_STRIP_TAGS.sub(" ", html or ""))
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in query.items() if not k.lower().startswith("utm_")}
    new_query = urlencode(filtered, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))


def _parse_published(entry: dict) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            ts = calendar.timegm(parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.now(timezone.utc)


def normalize_entry(entry: dict, source_name: str) -> dict | None:
    title = strip_html(entry.get("title") or "")
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None

    body_raw = entry.get("content") and entry["content"][0].get("value")
    if not body_raw:
        body_raw = entry.get("summary") or entry.get("description") or ""
    body = strip_html(body_raw)

    guid = (entry.get("id") or entry.get("guid") or "").strip()
    external_id = guid if guid else canonical_url(link)

    return {
        "external_id": external_id[:500],
        "title": title[:500],
        "body": body,
        "url": canonical_url(link)[:1000],
        "source_name": source_name,
        "published_at": _parse_published(entry),
    }


def _build_market_index() -> list[tuple[str, list[re.Pattern[str]]]]:
    """Return (market, [compiled patterns]) sorted by longest term first."""
    indexed: list[tuple[str, list[tuple[str, re.Pattern[str]]]]] = []
    for market, info in market_data_service.markets.items():
        terms: list[tuple[str, re.Pattern[str]]] = []
        base = info.get("base") or market.split("-")[0]
        if len(base) >= 2:
            terms.append((base, re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)))
        name = info.get("name")
        if name and len(name.strip()) >= 2:
            terms.append((name, re.compile(rf"\b{re.escape(name.strip())}\b", re.IGNORECASE)))
        if terms:
            terms.sort(key=lambda t: len(t[0]), reverse=True)
            indexed.append((market, [p for _, p in terms]))
    return [(m, pats) for m, pats in indexed]


def match_markets(text: str, index: list[tuple[str, list[re.Pattern[str]]]]) -> list[str]:
    matched: list[str] = []
    for market, patterns in index:
        if any(p.search(text) for p in patterns):
            matched.append(market)
    return matched


def article_to_news_item(article: NewsArticle) -> dict:
    return {
        "id": f"rss-{article.id}",
        "datetime": article.published_at.isoformat(),
        "title": article.title,
        "body": article.body,
        "language": ["en"],
        "url": article.url,
        "source": article.source_name,
    }


def seed_rss_feeds(db: Session) -> None:
    count = db.scalar(select(func.count()).select_from(RssFeed))
    if count and count > 0:
        return
    for url, name in INITIAL_FEEDS:
        db.add(RssFeed(url=url, name=name, enabled=True))
    db.commit()
    logger.info("Seeded %d RSS feeds", len(INITIAL_FEEDS))


def get_markets_with_articles(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(NewsArticleMarket.market, func.count())
        .group_by(NewsArticleMarket.market)
    ).all()
    return {row[0]: row[1] for row in rows}


def fetch_articles_for_market(db: Session, market: str, limit: int = 50) -> list[dict]:
    articles = db.scalars(
        select(NewsArticle)
        .join(NewsArticleMarket)
        .where(NewsArticleMarket.market == market)
        .order_by(NewsArticle.published_at.desc())
        .limit(limit)
    ).all()
    return [article_to_news_item(a) for a in articles]


class RssAggregatorService:
    def __init__(self) -> None:
        self.last_poll: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        db = SessionLocal()
        try:
            seed_rss_feeds(db)
        finally:
            db.close()
        self._task = asyncio.create_task(self._run(), name="rss-aggregator")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(5)  # let Bitvavo markets load before first match
        while True:
            try:
                await self.poll_all_feeds()
            except Exception as exc:
                logger.exception("RSS poll loop error: %s", exc)
                self.last_error = str(exc)
            await asyncio.sleep(POLL_INTERVAL)

    async def poll_all_feeds(self) -> None:
        db = SessionLocal()
        try:
            feeds = db.scalars(select(RssFeed).where(RssFeed.enabled.is_(True))).all()
            for feed in feeds:
                try:
                    await self._poll_feed(db, feed)
                except Exception as exc:
                    logger.warning("RSS feed %s failed: %s", feed.url, exc)
                    feed.last_error = str(exc)[:2000]
                    db.commit()
            self.last_poll = datetime.now(timezone.utc)
            self.last_error = None
        finally:
            db.close()

    async def poll_feed_by_id(self, feed_id: int) -> None:
        db = SessionLocal()
        try:
            feed = db.get(RssFeed, feed_id)
            if feed is None:
                raise ValueError(f"RSS feed {feed_id} not found")
            await self._poll_feed(db, feed)
        finally:
            db.close()

    async def _poll_feed(self, db: Session, feed: RssFeed) -> None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(feed.url, headers={"User-Agent": "de-BereBank/1.0"})
            resp.raise_for_status()
            content = resp.text

        parsed = feedparser.parse(content)
        index = _build_market_index()
        new_count = 0

        for entry in parsed.entries:
            normalized = normalize_entry(entry, feed.name)
            if normalized is None:
                continue

            existing = db.scalar(
                select(NewsArticle).where(
                    NewsArticle.feed_id == feed.id,
                    NewsArticle.external_id == normalized["external_id"],
                )
            )
            if existing is not None:
                continue

            search_text = f"{normalized['title']} {normalized['body']}"
            markets = match_markets(search_text, index)
            if not markets:
                continue

            article = NewsArticle(feed_id=feed.id, **normalized)
            db.add(article)
            db.flush()
            for market in markets:
                db.add(NewsArticleMarket(article_id=article.id, market=market))
            new_count += 1

        feed.last_fetched_at = datetime.now(timezone.utc)
        feed.last_error = None
        db.commit()
        logger.info("RSS feed %s: saved %d new articles", feed.name, new_count)

    def status(self) -> dict:
        db = SessionLocal()
        try:
            feed_count = db.scalar(select(func.count()).select_from(RssFeed)) or 0
            enabled_count = db.scalar(
                select(func.count()).select_from(RssFeed).where(RssFeed.enabled.is_(True))
            ) or 0
            article_count = db.scalar(select(func.count()).select_from(NewsArticle)) or 0
        finally:
            db.close()
        return {
            "feeds": feed_count,
            "enabled_feeds": enabled_count,
            "articles": article_count,
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
            "last_error": self.last_error,
        }


rss_aggregator_service = RssAggregatorService()
