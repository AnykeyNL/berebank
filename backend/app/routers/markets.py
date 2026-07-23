import time
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import BITVAVO_REST_URL
from ..database import get_db
from ..models import User
from ..schemas import MarketOut, NewsItemOut
from ..security import get_current_user
from ..services import analysis as analysis_service
from ..services.market_data import market_data_service
from ..services.rss_aggregator import fetch_articles_for_market, get_markets_with_articles, merge_news_items
from ..services.twelvedata import twelvedata_service

router = APIRouter(prefix="/markets", tags=["markets"])

# Cache briefly to avoid hammering Bitvavo when users flip markets/ranges.
_candle_cache: dict[str, tuple[float, list]] = {}
_analysis_cache: dict[str, tuple[float, dict]] = {}
_news_cache: dict[str, tuple[float, list]] = {}
_CANDLE_TTL = 60  # seconds
_NEWS_TTL = 300  # seconds; press releases change infrequently

# UI range → Bitvavo (interval, limit)
_RANGE_PARAMS: dict[str, tuple[str, int]] = {
    "1h": ("1m", 60),
    "1d": ("15m", 96),
    "1w": ("1h", 168),
    "30d": ("4h", 180),
    "90d": ("1d", 90),
    "180d": ("1d", 180),
    "365d": ("1d", 365),
}


async def _fetch_bitvavo_candles(market: str, interval: str, limit: int) -> list:
    async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
        resp = await client.get(
            f"/{market}/candles",
            params={"interval": interval, "limit": limit},
        )
        if resp.status_code != 200:
            raise HTTPException(502, "Could not fetch candles from Bitvavo")
        return sorted(resp.json(), key=lambda c: c[0])


def _change_pct(price: dict) -> Decimal | None:
    last, open_ = price.get("last"), price.get("open")
    if last is None or not open_:
        return None
    return ((last - open_) / open_ * 100).quantize(Decimal("0.01"))


@router.get("", response_model=list[MarketOut])
def list_markets(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    asset_class: Annotated[str | None, Query(pattern="^(crypto|stock|fund|commodity)$")] = None,
):
    article_counts = get_markets_with_articles(db)
    td_configured = twelvedata_service.api_key is not None
    out = []
    for market, info in sorted(market_data_service.markets.items()):
        if asset_class and info["asset_class"] != asset_class:
            continue
        price = market_data_service.get_price(market) or {}
        rss_count = article_counts.get(market, 0)
        if info["asset_class"] in ("stock", "fund"):
            has_news = rss_count > 0 or td_configured
        else:
            # Crypto and commodities rely on RSS matching only.
            has_news = rss_count > 0
        out.append(MarketOut(
            market=market,
            base=info["base"],
            quote=info["quote"],
            name=info.get("name"),
            listing=info.get("listing"),
            asset_class=info["asset_class"],
            last=price.get("last"),
            bid=price.get("bid"),
            ask=price.get("ask"),
            open=price.get("open"),
            change_24h_pct=_change_pct(price) if price else None,
            volume_quote=price.get("volume_quote"),
            market_open=price.get("market_open"),
            has_news=has_news,
        ))
    return out


@router.get("/{market}/candles")
async def get_candles(
    market: str,
    user: User = Depends(get_current_user),
    range_: Annotated[str, Query(alias="range")] = "1d",
):
    """OHLCV candles from Bitvavo for the requested range (oldest first).

    Each candle is [timestamp_ms, open, high, low, close, volume].
    Ranges: 1h, 1d, 1w, 30d, 90d, 180d, 365d.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _RANGE_PARAMS:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_RANGE_PARAMS)}")

    cache_key = f"{market}:{range_}"
    cached = _candle_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return cached[1]

    if market_info["asset_class"] == "crypto":
        interval, limit = _RANGE_PARAMS[range_]
        candles = await _fetch_bitvavo_candles(market, interval, limit)
    else:
        try:
            candles = await twelvedata_service.fetch_candles(market, range_)
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch candles from Twelve Data: {exc}")

    _candle_cache[cache_key] = (time.monotonic(), candles)
    return candles


# Ranges offered for analysis; 1h is excluded because 60 one-minute bars are
# too few for the slower indicators (SMA-50, MACD) to say anything useful.
_ANALYSIS_RANGES = ("1d", "1w", "30d", "90d", "180d", "365d")


@router.get("/{market}/analysis")
async def get_analysis(
    market: str,
    user: User = Depends(get_current_user),
    range_: Annotated[str, Query(alias="range")] = "30d",
):
    """Technical analysis for a market over the requested range.

    Runs five strategies (trend/moving averages, RSI, MACD, volatility with
    Bollinger Bands and ATR, support/resistance with volume) over OHLCV
    candles. Each strategy returns a signal (bullish/bearish/neutral, or
    "none" when there is not enough data), a structured reason, key values
    and overlay series. Ranges: 1d, 1w, 30d, 90d, 180d, 365d.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _ANALYSIS_RANGES:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_ANALYSIS_RANGES)}")

    cache_key = f"{market}:{range_}"
    cached = _analysis_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return cached[1]

    # Fetch the display window plus warm-up bars so indicators such as
    # SMA-50 are defined from the first visible bar.
    if market_info["asset_class"] == "crypto":
        interval, display_count = _RANGE_PARAMS[range_]
        candles = await _fetch_bitvavo_candles(
            market, interval, display_count + analysis_service.WARMUP_BARS
        )
    else:
        display_count = twelvedata_service._RANGE_PARAMS[range_][1]
        try:
            candles = await twelvedata_service.fetch_candles(
                market, range_, extra_bars=analysis_service.WARMUP_BARS
            )
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch candles from Twelve Data: {exc}")

    result = {
        "market": market,
        "range": range_,
        **analysis_service.analyze(candles, display_count),
    }
    _analysis_cache[cache_key] = (time.monotonic(), result)
    return result


@router.get("/{market}/news", response_model=list[NewsItemOut])
async def get_news(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
):
    """Recent news for a market (newest first).

    Combines RSS-matched articles with Twelve Data press releases for stocks/funds.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    cache_key = f"{market}:{limit}"
    cached = _news_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _NEWS_TTL:
        return cached[1]

    items: list[dict] = fetch_articles_for_market(db, market, limit=limit)

    if market_info["asset_class"] in ("stock", "fund"):
        try:
            td_items = await twelvedata_service.fetch_press_releases(market, limit)
            for row in td_items:
                items.append({
                    **row,
                    "source": "Twelve Data",
                    "url": None,
                })
        except Exception as exc:
            if not items:
                raise HTTPException(502, f"Could not fetch news: {exc}")

    merged = merge_news_items(items, limit)
    _news_cache[cache_key] = (time.monotonic(), merged)
    return merged
