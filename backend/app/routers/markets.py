import time
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import BITVAVO_REST_URL
from ..models import User
from ..schemas import MarketOut
from ..security import get_current_user
from ..services.market_data import market_data_service
from ..services.twelvedata import twelvedata_service

router = APIRouter(prefix="/markets", tags=["markets"])

# Cache briefly to avoid hammering Bitvavo when users flip markets/ranges.
_candle_cache: dict[str, tuple[float, list]] = {}
_CANDLE_TTL = 60  # seconds

# UI range → Bitvavo (interval, limit)
_RANGE_PARAMS: dict[str, tuple[str, int]] = {
    "1h": ("1m", 60),
    "1d": ("15m", 96),
    "1w": ("1h", 168),
    "30d": ("4h", 180),
    "365d": ("1d", 365),
}


def _change_pct(price: dict) -> Decimal | None:
    last, open_ = price.get("last"), price.get("open")
    if last is None or not open_:
        return None
    return ((last - open_) / open_ * 100).quantize(Decimal("0.01"))


@router.get("", response_model=list[MarketOut])
def list_markets(
    user: User = Depends(get_current_user),
    asset_class: Annotated[str | None, Query(pattern="^(crypto|stock|fund)$")] = None,
):
    out = []
    for market, info in sorted(market_data_service.markets.items()):
        if asset_class and info["asset_class"] != asset_class:
            continue
        price = market_data_service.get_price(market) or {}
        out.append(MarketOut(
            market=market,
            base=info["base"],
            quote=info["quote"],
            name=info.get("name"),
            asset_class=info["asset_class"],
            last=price.get("last"),
            bid=price.get("bid"),
            ask=price.get("ask"),
            open=price.get("open"),
            change_24h_pct=_change_pct(price) if price else None,
            volume_quote=price.get("volume_quote"),
            market_open=price.get("market_open"),
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
    Ranges: 1h, 1d, 1w, 30d, 365d.
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
        async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
            resp = await client.get(
                f"/{market}/candles",
                params={"interval": interval, "limit": limit},
            )
            if resp.status_code != 200:
                raise HTTPException(502, "Could not fetch candles from Bitvavo")
            candles = sorted(resp.json(), key=lambda c: c[0])
    else:
        try:
            candles = await twelvedata_service.fetch_candles(market, range_)
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch candles from Twelve Data: {exc}")

    _candle_cache[cache_key] = (time.monotonic(), candles)
    return candles
