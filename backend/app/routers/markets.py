import time
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..config import BITVAVO_REST_URL
from ..models import User
from ..schemas import MarketOut
from ..security import get_current_user
from ..services.bitvavo import bitvavo_service

router = APIRouter(prefix="/markets", tags=["markets"])

# Candles change at most every 15 minutes, so cache briefly to avoid
# hammering Bitvavo when users flip between markets.
_candle_cache: dict[str, tuple[float, list]] = {}
_CANDLE_TTL = 60  # seconds


def _change_pct(price: dict) -> Decimal | None:
    last, open_ = price.get("last"), price.get("open")
    if last is None or not open_:
        return None
    return ((last - open_) / open_ * 100).quantize(Decimal("0.01"))


@router.get("", response_model=list[MarketOut])
def list_markets(user: User = Depends(get_current_user)):
    out = []
    for market, info in sorted(bitvavo_service.markets.items()):
        price = bitvavo_service.get_price(market) or {}
        out.append(MarketOut(
            market=market,
            base=info["base"],
            quote=info["quote"],
            last=price.get("last"),
            bid=price.get("bid"),
            ask=price.get("ask"),
            open=price.get("open"),
            change_24h_pct=_change_pct(price) if price else None,
            volume_quote=price.get("volume_quote"),
        ))
    return out


@router.get("/{market}/candles")
async def get_candles(market: str, user: User = Depends(get_current_user)):
    """Last 24 hours of 15-minute OHLCV candles from Bitvavo (oldest first).

    Each candle is [timestamp_ms, open, high, low, close, volume].
    """
    market = market.upper()
    if market not in bitvavo_service.markets:
        raise HTTPException(404, f"Unknown market: {market}")

    cached = _candle_cache.get(market)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return cached[1]

    async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
        resp = await client.get(f"/{market}/candles", params={"interval": "15m", "limit": 96})
        if resp.status_code != 200:
            raise HTTPException(502, "Could not fetch candles from Bitvavo")
        candles = sorted(resp.json(), key=lambda c: c[0])

    _candle_cache[market] = (time.monotonic(), candles)
    return candles
