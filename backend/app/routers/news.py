import asyncio
import math
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..database import SessionLocal
from ..models import User
from ..schemas import NewsPageOut
from ..security import get_current_user
from ..services.rss_aggregator import fetch_recent_rss_articles, merge_news_items
from ..services.twelvedata import twelvedata_service

router = APIRouter(prefix="/news", tags=["news"])

MAX_PAGES = 5
MAX_PAGE_SIZE = 20
MAX_ITEMS = MAX_PAGES * MAX_PAGE_SIZE
_FEED_TTL = 300  # seconds

_feed_cache: tuple[float, list[dict]] | None = None
_feed_lock = asyncio.Lock()


async def _build_feed() -> list[dict]:
    db = SessionLocal()
    try:
        items = fetch_recent_rss_articles(db, limit=MAX_ITEMS * 2)
    finally:
        db.close()

    try:
        press_releases = await twelvedata_service.fetch_recent_press_releases(
            limit_per_market=2,
            max_markets=25,
            timeout=12,
        )
        items.extend(press_releases)
    except Exception:
        pass

    return merge_news_items(items, MAX_ITEMS)


async def _get_feed() -> list[dict]:
    global _feed_cache
    now = time.monotonic()
    if _feed_cache and now - _feed_cache[0] < _FEED_TTL:
        return _feed_cache[1]

    async with _feed_lock:
        now = time.monotonic()
        if _feed_cache and now - _feed_cache[0] < _FEED_TTL:
            return _feed_cache[1]
        feed = await _build_feed()
        _feed_cache = (time.monotonic(), feed)
        return feed


@router.get("", response_model=NewsPageOut)
async def list_news(
    user: User = Depends(get_current_user),
    page: Annotated[int, Query(ge=1, le=MAX_PAGES)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
):
    """Recent news across all markets (RSS feeds and press releases), paginated."""
    feed = await _get_feed()
    total_count = len(feed)
    total_pages = min(MAX_PAGES, max(1, math.ceil(total_count / page_size))) if total_count else 1
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    return NewsPageOut(
        items=feed[start:end],
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        total_count=total_count,
    )
