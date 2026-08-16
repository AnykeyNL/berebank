import asyncio
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..config import BITVAVO_REST_URL
from ..database import get_db
from ..models import User
from ..schemas import MarketOut, NewsItemOut
from ..security import get_current_user
from ..services import analysis as analysis_service
from ..services import fable5_analysis as fable5_analysis_service
from ..services import gtp56sol_analysis as gtp56sol_analysis_service
from ..services import kimi_analysis as kimi_analysis_service
from ..services import opus_analysis as opus_analysis_service
from ..services import opus_calibration as opus_calibration_service
from ..services import opus_store
from ..services.backtest import track_record
from ..services.candle_store import (
    CandleHistorySummary,
    completed_history_summaries,
    completed_history_summary,
    ensure_gtp56sol_deep_history,
    load_completed_daily_candles,
    load_recent_daily_candles,
)
from ..services.fees import get_30d_volume, get_fee_rates
from ..services import market_calendar
from ..services.market_data import market_data_service
from ..services.payload import plain_decimal, shape_analysis
from ..services.rss_aggregator import fetch_articles_for_market, get_markets_with_articles, merge_news_items
from ..services.td_context import get_macro_context, get_market_context, serialize_context
from ..services.twelvedata import twelvedata_service

router = APIRouter(prefix="/markets", tags=["markets"])

# Cache briefly to avoid hammering Bitvavo when users flip markets/ranges.
_candle_cache: dict[str, tuple[float, list]] = {}
_analysis_cache: dict[str, tuple[float, dict]] = {}
_gtp56sol_cache: dict[tuple, tuple[float, dict]] = {}
_gtp56sol_inflight_locks: dict[
    tuple, tuple[asyncio.AbstractEventLoop, asyncio.Lock]
] = {}
_gtp56sol_cpu_semaphores: dict[
    str, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]
] = {}
_kimi_cache: dict[str, tuple[float, dict]] = {}
_technical_outlooks_cache: tuple[float, dict] | None = None
_kimi_outlooks_cache: tuple[float, dict] | None = None
_kimi_track_record_cache: dict[str, tuple[float, dict | None]] = {}
_fable5_cache: dict[str, tuple[float, dict]] = {}
_fable5_outlooks_cache: tuple[float, dict] | None = None
_fable5_track_record_cache: dict[str, tuple[float, dict | None]] = {}
_gtp56sol_outlooks_cache: dict[str, tuple[float, dict]] = {}
_opus_scores_cache: tuple[float, dict] | None = None
_opus_outlooks_cache: dict[str, tuple[float, dict]] = {}
_opus_cache: dict[str, tuple[float, dict]] = {}
_opus_track_record_cache: dict[str, tuple[float, dict | None]] = {}
_news_cache: dict[str, tuple[float, list]] = {}
_CANDLE_TTL = 60  # seconds
_CANDLE_HISTORY_TTL = 3600  # seconds; pages behind the live window never change
_CANDLE_CACHE_MAX = 500  # bound the cache: paged keys carry unbounded timestamps
_GTP56SOL_TTL = 3600  # daily stored inputs change slowly
_GTP56SOL_PEER_CAP = 8
_GTP56SOL_MAX_CPU_FORECASTS = 2
_TECHNICAL_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_KIMI_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_FABLE5_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_GTP56SOL_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_GTP56SOL_OUTLOOKS_WORKERS = 4
# Opus scores completed daily bars, so a fresh pass per quarter hour is plenty.
_OPUS_TTL = 900
_OPUS_OUTLOOKS_TTL = 900
_TRACK_RECORD_TTL = 3600  # seconds; recomputed from stored daily candles
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


async def _fetch_bitvavo_candles(
    market: str, interval: str, limit: int, end: int | None = None
) -> list:
    params: dict[str, object] = {"interval": interval, "limit": limit}
    if end is not None:
        # Bitvavo's `end` is exclusive, matching our own bound.
        params["end"] = end
    async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
        resp = await client.get(f"/{market}/candles", params=params)
        if resp.status_code != 200:
            raise HTTPException(502, "Could not fetch candles from Bitvavo")
        return sorted(resp.json(), key=lambda c: c[0])


def _candle_cache_key(market: str, range_: str, end: int | None) -> str:
    return f"{market}:{range_}:{end or ''}"


def _candle_cache_ttl(end: int | None) -> int:
    """Pages behind the live window are immutable, so they can be held longer."""
    return _CANDLE_HISTORY_TTL if end else _CANDLE_TTL


def _store_candles(key: str, candles: list) -> None:
    _candle_cache[key] = (time.monotonic(), candles)
    while len(_candle_cache) > _CANDLE_CACHE_MAX:
        _candle_cache.pop(next(iter(_candle_cache)))


def _change_pct(price: dict) -> Decimal | None:
    last, open_ = price.get("last"), price.get("open")
    if last is None or not open_:
        return None
    return ((last - open_) / open_ * 100).quantize(Decimal("0.01"))


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat().replace("+00:00", "Z")


def _session_states() -> dict[str, dict]:
    """One calendar lookup per asset class, reused across every market."""
    return {
        name: market_calendar.session_state(name)
        for name in ("crypto", "stock", "fund", "commodity")
    }


@router.get("/hours")
def get_market_hours(
    user: User = Depends(get_current_user),
    market: str | None = None,
    asset_class: Annotated[str | None, Query(pattern="^(crypto|stock|fund|commodity)$")] = None,
):
    """Trading hours per asset class: open now, and when that next changes.

    `is_open` is the answer to act on: it follows the live feed where there is
    one, since that also reflects halts, and the calendar otherwise.
    `calendar_open` and `live_market_open` are reported separately so a
    disagreement is visible rather than hidden. Timestamps are UTC ISO 8601 and
    are null for crypto, which never closes.
    """
    wanted: list[tuple[str, str | None]] = []
    if market:
        market = market.upper()
        info = market_data_service.get_market(market)
        if info is None:
            raise HTTPException(404, f"Unknown market: {market}")
        wanted.append((info["asset_class"], market))
    elif asset_class:
        wanted.append((asset_class, None))
    else:
        wanted = [(name, None) for name in ("crypto", "stock", "fund", "commodity")]

    states = _session_states()
    hours = []
    for name, symbol in wanted:
        state = states[name]
        live_open = None
        if symbol is not None:
            live_open = (market_data_service.get_price(symbol) or {}).get("market_open")
        elif not state["always_open"]:
            # Any market in the class carries the same flag; take the first.
            for candidate, info in market_data_service.markets.items():
                if info["asset_class"] == name:
                    live_open = (market_data_service.get_price(candidate) or {}).get("market_open")
                    break
        market_calendar.note_disagreement(name, live_open)
        hours.append({
            "asset_class": name,
            "market": symbol,
            "calendar": state["calendar"],
            "timezone": state["timezone"],
            "always_open": state["always_open"],
            "is_open": state["is_open"] if live_open is None else live_open,
            "calendar_open": state["is_open"],
            "live_market_open": live_open,
            "next_open": _iso(state["next_open"]),
            "next_close": _iso(state["next_close"]),
            "current_session_end": _iso(state["current_session_end"]),
        })
    return {
        "server_time_utc": _iso(datetime.now(timezone.utc)),
        "hours": hours,
    }


@router.get("", response_model=list[MarketOut])
def list_markets(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    asset_class: Annotated[str | None, Query(pattern="^(crypto|stock|fund|commodity)$")] = None,
):
    article_counts = get_markets_with_articles(db)
    td_configured = twelvedata_service.api_key is not None
    states = _session_states()
    out = []
    for market, info in sorted(market_data_service.markets.items()):
        if asset_class and info["asset_class"] != asset_class:
            continue
        state = states[info["asset_class"]]
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
            tick_size=plain_decimal(info.get("tick_size")),
            amount_decimals=info.get("amount_decimals"),
            min_order_base=plain_decimal(info.get("min_base")),
            next_open=_iso(state["next_open"]),
            next_close=_iso(state["next_close"]),
        ))
    return out


@router.get("/technical-outlooks")
def get_technical_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Technical analysis outlook summary for every market, for list views.

    Computed from stored daily candles using the five base strategies (trend,
    RSI, MACD, volatility, support/resistance). Values: direction, score
    (-100..+100), confidence and regime.
    """
    global _technical_outlooks_cache
    if _technical_outlooks_cache and time.monotonic() - _technical_outlooks_cache[0] < _TECHNICAL_OUTLOOKS_TTL:
        return _technical_outlooks_cache[1]

    outlooks: dict[str, dict] = {}
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) < 60:
            continue
        strategies = analysis_service.analyze(candles, len(candles))["strategies"]
        outlook = analysis_service.compute_technical_outlook(strategies)
        outlooks[market] = {
            "direction": outlook["direction"],
            "score": outlook["score"],
            "confidence": outlook["confidence"],
            "regime": outlook["regime"],
        }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "outlooks": outlooks,
    }
    _technical_outlooks_cache = (time.monotonic(), result)
    return result


@router.get("/kimi-outlooks")
async def get_kimi_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """KimiK3 direction outlook summary for every market, for list views.

    Computed from the stored daily candles (harvested in the background),
    so a market appears once enough history has been collected. Values:
    direction, score (-100..+100), buy_score / sell_score (0..100 shares of
    active regime-weighted weight voting bullish resp. bearish), confidence
    and regime — same engine as the per-market KimiK3 analysis endpoint.
    """
    global _kimi_outlooks_cache
    if _kimi_outlooks_cache and time.monotonic() - _kimi_outlooks_cache[0] < _KIMI_OUTLOOKS_TTL:
        return _kimi_outlooks_cache[1]

    macro = await get_macro_context()
    from ..services.crypto_context import get_macro_context as get_crypto_macro_context
    crypto_macro = await get_crypto_macro_context()
    outlooks: dict[str, dict] = {}
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) < 60:
            continue
        asset_class = market_data_service.get_market(market)["asset_class"]
        shared = crypto_macro if asset_class == "crypto" else macro
        context = await _kimi_context(market, asset_class, shared)
        outlook = kimi_analysis_service.analyze_kimi(candles, len(candles), context)["outlook"]
        outlooks[market] = {
            "direction": outlook["direction"],
            "score": outlook["score"],
            "buy_score": outlook["buy_score"],
            "sell_score": outlook["sell_score"],
            "confidence": outlook["confidence"],
            "regime": outlook["regime"],
        }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "outlooks": outlooks,
    }
    _kimi_outlooks_cache = (time.monotonic(), result)
    return result


@router.get("/fable5-outlooks")
async def get_fable5_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fable5 direction outlook summary for every market, for list views.

    Computed from the stored daily candles (harvested in the background),
    so a market appears once enough history has been collected. Values:
    direction, score (-100..+100), buy_score / sell_score (0..100 shares of
    signal weight voting bullish resp. bearish), confidence and regime —
    same engine as the per-market Fable5 analysis endpoint.
    """
    global _fable5_outlooks_cache
    if _fable5_outlooks_cache and time.monotonic() - _fable5_outlooks_cache[0] < _FABLE5_OUTLOOKS_TTL:
        return _fable5_outlooks_cache[1]

    macro = await get_macro_context()
    from ..services.crypto_context import get_macro_context as get_crypto_macro_context
    crypto_macro = await get_crypto_macro_context()
    outlooks: dict[str, dict] = {}
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) < 60:
            continue
        asset_class = market_data_service.get_market(market)["asset_class"]
        shared = crypto_macro if asset_class == "crypto" else macro
        # Copy so the asset-class tags never leak into the shared macro cache.
        context = (
            {**shared, "asset_class": asset_class, "base": market.split("-", 1)[0]}
            if shared is not None
            else None
        )
        outlook = fable5_analysis_service.analyze_fable5(candles, len(candles), context)["outlook"]
        outlooks[market] = {
            "direction": outlook["direction"],
            "score": outlook["score"],
            "buy_score": outlook["buy_score"],
            "sell_score": outlook["sell_score"],
            "confidence": outlook["confidence"],
            "regime": outlook["regime"],
        }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "outlooks": outlooks,
    }
    _fable5_outlooks_cache = (time.monotonic(), result)
    return result


def _opus_scores(db: Session) -> dict:
    """Cached cross-sectional scoring pass, shared by every Opus endpoint.

    One pass scores all markets on all three horizons; the fee-dependent part is
    applied per request so a user's own fee tier never pollutes the cache.
    """
    global _opus_scores_cache
    if _opus_scores_cache and time.monotonic() - _opus_scores_cache[0] < _OPUS_TTL:
        return _opus_scores_cache[1]
    scores = opus_store.compute_scores(db)
    _opus_scores_cache = (time.monotonic(), scores)
    return scores


def _opus_fee_rates(db: Session, user: User) -> tuple[float, float]:
    """The connected user's own maker/taker rates, in percent."""
    try:
        volume = get_30d_volume(db, user.account.id)
        maker, taker = get_fee_rates(volume)
        return float(maker) * 100, float(taker) * 100
    except Exception:
        return opus_analysis_service.DEFAULT_MAKER_PCT, opus_analysis_service.DEFAULT_TAKER_PCT


def _opus_finalized_rows(
    db: Session,
    user: User,
    horizon: str,
    scores: dict,
    *,
    holdings: set[str] | None = None,
) -> list[dict]:
    maker_pct, taker_pct = _opus_fee_rates(db, user)
    rows = []
    for row in scores["rows"].get(horizon) or []:
        price = market_data_service.get_price(row["market"]) or {}
        rows.append(opus_analysis_service.finalize_row(
            row,
            taker_pct=taker_pct,
            maker_pct=maker_pct,
            market_open=price.get("market_open"),
            days_since_close=row.get("days_since_close"),
            held=bool(holdings and row["market"] in holdings),
        ))
    return opus_analysis_service.rank_rows(rows)


@router.get("/opus-rankings")
async def get_opus_rankings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    horizon: str = opus_calibration_service.DEFAULT_HORIZON,
    asset_class: Annotated[str | None, Query(pattern="^(crypto|stock|fund|commodity)$")] = None,
    side: Annotated[str, Query(pattern="^(buy|sell)$")] = "buy",
    limit: Annotated[int, Query(ge=1, le=600)] = 50,
):
    """Opus buy/sell ranking of every market for a 1-day to 4-week horizon.

    Opus scores each market against its peers on the same day rather than
    against absolute thresholds, using feature weights learned from the stored
    daily history (walk-forward information coefficients). The composite is
    mapped to an expected return in percent, the peer-group drift is added
    through the market's beta, and real Bitvavo fees for the requesting user's
    own tier are subtracted — so `net_edge_pct` is what is actually left over
    after trading costs. Rows are ordered best-first for the requested side and
    carry the tradability gates (liquidity, data freshness, market hours,
    low-volatility) that decide whether acting is sensible at all.
    """
    if horizon not in opus_calibration_service.HORIZONS:
        allowed = ", ".join(opus_calibration_service.HORIZONS)
        raise HTTPException(400, f"Invalid horizon: {horizon}. Use one of {allowed}")

    scores = await run_in_threadpool(_opus_scores, db)
    rows = await run_in_threadpool(_opus_finalized_rows, db, user, horizon, scores)
    basket = opus_analysis_service.select_basket(rows)

    if asset_class:
        rows = [row for row in rows if row["asset_class"] == asset_class]
    rank_field = "buy_rank" if side == "buy" else "sell_rank"
    rows.sort(key=lambda row: row[rank_field])
    return {
        "generated_at": scores["generated_at"],
        "engine_version": scores["engine_version"],
        "horizon": horizon,
        "side": side,
        "regimes": scores["regimes"],
        "group_days": scores["group_days"],
        "macro": scores["macro"],
        "calibrated": scores["calibrated"],
        "markets": scores["markets"],
        "basket": basket,
        "rankings": rows[:limit],
    }


@router.get("/opus-outlooks")
async def get_opus_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    horizon: str = opus_calibration_service.DEFAULT_HORIZON,
):
    """Opus outlook summary for every market, for list views.

    A projection of the Opus ranking: direction, score (-100..+100), buy/sell
    scores (0..100 fee-aware conviction), confidence, regime and the
    recommended action — same engine as the per-market Opus analysis endpoint.
    """
    if horizon not in opus_calibration_service.HORIZONS:
        allowed = ", ".join(opus_calibration_service.HORIZONS)
        raise HTTPException(400, f"Invalid horizon: {horizon}. Use one of {allowed}")

    cached = _opus_outlooks_cache.get(horizon)
    if cached and time.monotonic() - cached[0] < _OPUS_OUTLOOKS_TTL:
        return cached[1]

    scores = await run_in_threadpool(_opus_scores, db)
    rows = await run_in_threadpool(_opus_finalized_rows, db, user, horizon, scores)
    result = {
        "generated_at": scores["generated_at"],
        "horizon": horizon,
        "outlooks": {
            row["market"]: {
                "direction": row["direction"],
                "score": row["score"],
                "buy_score": row["buy_score"],
                "sell_score": row["sell_score"],
                "confidence": row["confidence"],
                "regime": row["regime"],
                "action": row["action"],
                "buy_rank": row["buy_rank"],
                "sell_rank": row["sell_rank"],
            }
            for row in rows
        },
    }
    _opus_outlooks_cache[horizon] = (time.monotonic(), result)
    return result


@router.get("/gtp56sol-outlooks")
def get_gtp56sol_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    horizon: str = "1w",
):
    """GTP56Sol forecast summary for every market, for list views.

    Uses the fast asset-only outlook path (no walk-forward validation) over
    the same 400-bar daily window as Kimi/Fable5 outlooks. Values: direction,
    net score (-100..+100 from Up minus Down probability), and confidence.
    """
    if horizon not in gtp56sol_analysis_service.HORIZONS:
        allowed = ", ".join(gtp56sol_analysis_service.HORIZONS)
        raise HTTPException(400, f"Invalid horizon: {horizon}. Use one of {allowed}")

    global _gtp56sol_outlooks_cache
    cached = _gtp56sol_outlooks_cache.get(horizon)
    if cached and time.monotonic() - cached[0] < _GTP56SOL_OUTLOOKS_TTL:
        return cached[1]

    prepared: list[tuple[str, list]] = []
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) >= 60:
            prepared.append((market, candles))

    outlooks: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_GTP56SOL_OUTLOOKS_WORKERS) as pool:
        futures = {
            pool.submit(
                gtp56sol_analysis_service.forecast_outlook,
                candles,
                horizon,
            ): market
            for market, candles in prepared
        }
        for future, market in futures.items():
            try:
                payload = future.result()
            except Exception:
                continue
            if payload.get("status") != "ok":
                continue
            outlooks[market] = {
                "direction": payload["direction"],
                "score": payload["score"],
                "confidence": payload["confidence"],
            }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "horizon": horizon,
        "outlooks": outlooks,
    }
    _gtp56sol_outlooks_cache[horizon] = (time.monotonic(), result)
    return result


@router.get("/{market}/candles")
async def get_candles(
    market: str,
    user: User = Depends(get_current_user),
    range_: Annotated[str, Query(alias="range")] = "1d",
    end: Annotated[int | None, Query(ge=0)] = None,
):
    """OHLCV candles from Bitvavo for the requested range (oldest first).

    Each candle is [timestamp_ms, open, high, low, close, volume].
    Ranges: 1h, 1d, 1w, 30d, 90d, 180d, 365d.

    ``end`` (epoch ms, exclusive) returns the page of bars just before it at
    the range's own interval, so charts can extend history on zoom out.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _RANGE_PARAMS:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_RANGE_PARAMS)}")

    cache_key = _candle_cache_key(market, range_, end)
    cached = _candle_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _candle_cache_ttl(end):
        return cached[1]

    if market_info["asset_class"] == "crypto":
        interval, limit = _RANGE_PARAMS[range_]
        candles = await _fetch_bitvavo_candles(market, interval, limit, end=end)
    else:
        try:
            candles = await twelvedata_service.fetch_candles(market, range_, end_ms=end)
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch candles from Twelve Data: {exc}")

    _store_candles(cache_key, candles)
    return candles


# Ranges offered for analysis; 1h is excluded because 60 one-minute bars are
# too few for the slower indicators (SMA-50, MACD) to say anything useful.
_ANALYSIS_RANGES = ("1d", "1w", "30d", "90d", "180d", "365d")


@router.get("/{market}/analysis")
async def get_analysis(
    market: str,
    user: User = Depends(get_current_user),
    range_: Annotated[str, Query(alias="range")] = "30d",
    verbose: bool = True,
):
    """Technical analysis for a market over the requested range.

    Runs five strategies (trend/moving averages, RSI, MACD, volatility with
    Bollinger Bands and ATR, support/resistance with volume) over OHLCV
    candles. Each strategy returns a signal (bullish/bearish/neutral, or
    "none" when there is not enough data), a structured reason, key values
    and overlay series. Ranges: 1d, 1w, 30d, 90d, 180d, 365d.

    ``verbose=false`` omits the chart payload (candles and indicator series)
    and keeps the signals, values and explanations.
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
        return shape_analysis(cached[1], verbose)

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
    return shape_analysis(result, verbose)


def _kimi_track_record(db: Session, market: str) -> dict | None:
    cached = _kimi_track_record_cache.get(market)
    if cached and time.monotonic() - cached[0] < _TRACK_RECORD_TTL:
        return cached[1]
    record = track_record(
        load_recent_daily_candles(db, market),
        kimi_analysis_service.analyze_kimi,
    )
    _kimi_track_record_cache[market] = (time.monotonic(), record)
    return record


async def _kimi_context(market: str, asset_class: str, shared_context: dict | None) -> dict | None:
    """Shared context tagged for the KimiK3 engine.

    Returns a copy tagged with ``asset_class``/``base`` so the engine can
    gate asset-class signals; the shared macro caches are never mutated.
    Crypto-linked funds (IBIT) additionally merge the shared crypto macro
    context and BTC derivatives so they vote with crypto signals.
    """
    base = market.split("-", 1)[0]
    if base in kimi_analysis_service.CRYPTO_LINKED_BASES:
        from ..services.coinglass import get_symbol_derivatives
        from ..services.crypto_context import get_macro_context as get_crypto_macro_context

        crypto_macro, btc_derivatives = await asyncio.gather(
            get_crypto_macro_context(),
            get_symbol_derivatives("BTC"),
        )
        merged = {
            **(shared_context or {}),
            **(crypto_macro or {}),
            **{k: v for k, v in btc_derivatives.items() if k != "coinglass_symbol"},
        }
        return {
            **merged,
            "context_type": "crypto",
            "asset_class": asset_class,
            "base": base,
        }
    if shared_context is None:
        return None
    return {**shared_context, "asset_class": asset_class, "base": base}


def _fable5_track_record(db: Session, market: str) -> dict | None:
    cached = _fable5_track_record_cache.get(market)
    if cached and time.monotonic() - cached[0] < _TRACK_RECORD_TTL:
        return cached[1]
    record = track_record(
        load_recent_daily_candles(db, market),
        fable5_analysis_service.analyze_fable5,
    )
    _fable5_track_record_cache[market] = (time.monotonic(), record)
    return record


def _select_gtp56sol_peer_markets(
    db: Session,
    primary_market: str,
    asset_class: str,
    *,
    cap: int = _GTP56SOL_PEER_CAP,
) -> list[str]:
    """Choose same-class peers by completed history length, then market name."""
    return [
        signature[0]
        for signature in _select_gtp56sol_peer_summaries(
            db,
            primary_market,
            asset_class,
            cap=cap,
        )
    ]


def _select_gtp56sol_peer_summaries(
    db: Session,
    primary_market: str,
    asset_class: str,
    *,
    cap: int = _GTP56SOL_PEER_CAP,
) -> tuple[tuple[str, int, int, int], ...]:
    """Select peer cache signatures using one grouped aggregate query."""
    if cap <= 0:
        return ()
    eligible = sorted(
        market
        for market, info in market_data_service.markets.items()
        if info.get("asset_class") == asset_class and market != primary_market
    )
    if not eligible:
        return ()
    summaries = completed_history_summaries(db, eligible)
    selected = sorted(
        (
            (market, summary)
            for market, summary in summaries.items()
            if summary.count > 0
        ),
        key=lambda item: (-item[1].count, item[0]),
    )[:cap]
    return tuple(
        (market, summary.first_ts, summary.last_ts, summary.count)
        for market, summary in selected
    )


def _gtp56sol_history_signature(candles) -> tuple[int | None, int | None, int]:
    return (
        int(candles[0][0]) if candles else None,
        int(candles[-1][0]) if candles else None,
        len(candles),
    )


def _gtp56sol_summary_signature(
    summary: CandleHistorySummary,
) -> tuple[int | None, int | None, int]:
    return (summary.first_ts, summary.last_ts, summary.count)


def _gtp56sol_inflight_lock(cache_key: tuple) -> asyncio.Lock:
    """Return a lock bound to the current production/test event loop."""
    loop = asyncio.get_running_loop()
    existing = _gtp56sol_inflight_locks.get(cache_key)
    if existing is None or existing[0] is not loop:
        lock = asyncio.Lock()
        _gtp56sol_inflight_locks[cache_key] = (loop, lock)
        return lock
    return existing[1]


def _gtp56sol_cpu_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    existing = _gtp56sol_cpu_semaphores.get("forecast")
    if existing is None or existing[0] is not loop:
        semaphore = asyncio.Semaphore(_GTP56SOL_MAX_CPU_FORECASTS)
        _gtp56sol_cpu_semaphores["forecast"] = (loop, semaphore)
        return semaphore
    return existing[1]


async def _run_gtp56sol_forecast(
    candles,
    horizon: str,
    fallback,
    *,
    forecast_func=None,
    context=None,
) -> dict:
    """Run pure forecast work off-loop with bounded process concurrency."""
    forecast_func = forecast_func or gtp56sol_analysis_service.forecast
    async with _gtp56sol_cpu_semaphore():
        return await run_in_threadpool(
            forecast_func,
            candles,
            horizon,
            fallback_candles_by_market=fallback,
            context=context,
        )


def _gtp56sol_cached(cache_key: tuple, now_monotonic: float) -> dict | None:
    cached = _gtp56sol_cache.get(cache_key)
    if cached and now_monotonic - cached[0] < _GTP56SOL_TTL:
        return copy.deepcopy(cached[1])
    return None


@router.get("/{market}/gtp56sol-analysis")
async def get_gtp56sol_analysis(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    horizon: str = "1w",
):
    """Historical-pattern probabilities for 1/5/21 forward session bars."""
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")
    if horizon not in gtp56sol_analysis_service.HORIZONS:
        allowed = ", ".join(gtp56sol_analysis_service.HORIZONS)
        raise HTTPException(400, f"Invalid horizon: {horizon}. Use one of {allowed}")

    # Only the requested primary market gets a conservative lazy deep-history
    # attempt. Peer histories are never fetched here.
    await ensure_gtp56sol_deep_history(db, market, market_info["asset_class"])
    primary_summary = completed_history_summary(db, market)
    peer_plan_ready = not gtp56sol_analysis_service.has_sufficient_asset_history_count(
        primary_summary.count,
        horizon,
    )
    peer_signature = (
        _select_gtp56sol_peer_summaries(
            db, market, market_info["asset_class"]
        )
        if peer_plan_ready
        else ()
    )

    while True:
        cache_key = (
            market,
            horizon,
            _gtp56sol_summary_signature(primary_summary),
            peer_signature,
            gtp56sol_analysis_service.ENGINE_VERSION,
        )
        now_monotonic = time.monotonic()
        cached = _gtp56sol_cached(cache_key, now_monotonic)
        if cached is not None:
            return cached

        async with _gtp56sol_inflight_lock(cache_key):
            now_monotonic = time.monotonic()
            cached = _gtp56sol_cached(cache_key, now_monotonic)
            if cached is not None:
                return cached

            candles = tuple(
                tuple(candle)
                for candle in load_completed_daily_candles(db, market)
            )
            actual_primary_signature = _gtp56sol_history_signature(candles)
            if actual_primary_signature != _gtp56sol_summary_signature(primary_summary):
                primary_summary = CandleHistorySummary(
                    first_ts=actual_primary_signature[0],
                    last_ts=actual_primary_signature[1],
                    count=actual_primary_signature[2],
                )
                peer_plan_ready = False
                peer_signature = ()
                continue

            exact_needs_fallback = (
                not gtp56sol_analysis_service.has_sufficient_asset_history(
                    candles,
                    horizon,
                )
            )
            if exact_needs_fallback and not peer_plan_ready:
                peer_signature = _select_gtp56sol_peer_summaries(
                    db, market, market_info["asset_class"]
                )
                peer_plan_ready = True
                continue

            fallback = None
            if exact_needs_fallback and peer_signature:
                fallback_rows: dict[str, tuple[tuple, ...]] = {}
                signatures_match = True
                for peer, first_ts, last_ts, count in peer_signature:
                    if peer == market:
                        continue
                    peer_candles = tuple(
                        tuple(candle)
                        for candle in load_completed_daily_candles(db, peer)
                    )
                    if _gtp56sol_history_signature(peer_candles) != (
                        first_ts,
                        last_ts,
                        count,
                    ):
                        signatures_match = False
                        break
                    fallback_rows[peer] = peer_candles
                if not signatures_match:
                    peer_signature = _select_gtp56sol_peer_summaries(
                        db, market, market_info["asset_class"]
                    )
                    continue
                fallback = fallback_rows or None

            td_context = await get_market_context(market, market_info["asset_class"])

            forecast_payload = await _run_gtp56sol_forecast(
                candles,
                horizon,
                fallback,
                context=td_context,
            )
            result = {
                "market": market,
                "asset_class": market_info["asset_class"],
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "context": serialize_context(td_context),
                **forecast_payload,
            }
            # Expired and input-obsolete entries are safe but unnecessary.
            for key, (created, _) in list(_gtp56sol_cache.items()):
                if (
                    created + _GTP56SOL_TTL <= now_monotonic
                    or key[:2] == (market, horizon)
                ):
                    _gtp56sol_cache.pop(key, None)
                    if key != cache_key:
                        _gtp56sol_inflight_locks.pop(key, None)
            _gtp56sol_cache[cache_key] = (now_monotonic, copy.deepcopy(result))
            return copy.deepcopy(result)


@router.get("/{market}/kimi-analysis")
async def get_kimi_analysis(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range_: Annotated[str, Query(alias="range")] = "30d",
    verbose: bool = True,
):
    """KimiK3 direction outlook for a market over the requested range.

    Blends eight price strategies (the five base strategies plus ADX trend
    strength, dual-horizon momentum and a slow stochastic) with asset-class
    context signals — Fear & Greed, liquidity, funding level and 4h funding
    momentum, price-confirmed open interest on 1h/4h/24h windows, long/short
    positioning and liquidations for crypto; VIX, yield curve, sector
    relative strength, earnings proximity and insider flow for stocks;
    safe-haven aware VIX/yield logic for gold, Treasuries and precious
    metals; crypto macro signals for IBIT — using regime-aware weights into
    a single outlook: direction (bullish/bearish/neutral), a score from
    -100 to +100, buy/sell scores (0..100 shares of active weight voting
    each way), a confidence level and per-strategy contributions. Includes
    a track record (hit rate of past outlooks on this market, computed from
    stored daily candles) once enough history has been harvested. Ranges:
    1d, 1w, 30d, 90d, 180d, 365d.

    ``verbose=false`` omits the chart payload (candles and indicator series)
    and keeps the outlook, signals, values and explanations.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _ANALYSIS_RANGES:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_ANALYSIS_RANGES)}")

    cache_key = f"{market}:{range_}"
    cached = _kimi_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return shape_analysis(cached[1], verbose)

    # Same candle fetch as the analysis endpoint: display window plus
    # warm-up bars so indicators are defined from the first visible bar.
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

    td_context = await get_market_context(market, market_info["asset_class"])
    kimi_context = await _kimi_context(market, market_info["asset_class"], td_context)
    result = {
        "market": market,
        "range": range_,
        **kimi_analysis_service.analyze_kimi(candles, display_count, kimi_context),
        "context": serialize_context(kimi_context),
        "track_record": _kimi_track_record(db, market),
    }
    _kimi_cache[cache_key] = (time.monotonic(), result)
    return shape_analysis(result, verbose)


@router.get("/{market}/fable5-analysis")
async def get_fable5_analysis(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range_: Annotated[str, Query(alias="range")] = "30d",
    verbose: bool = True,
):
    """Fable5 direction outlook for a market over the requested range.

    Blends the five analysis strategies plus dual-horizon momentum, a slow
    stochastic oscillator and ADX trend strength with asset-class specific
    context signals (VIX and yield curve for stocks/funds/commodities; Fear &
    Greed, liquidity, funding, price-confirmed open interest, long/short
    positioning and liquidations for crypto; sector relative strength and
    earnings proximity for stocks) — all with fixed importance weights — into
    a single outlook: direction (bullish/bearish/neutral), a score from -100
    to +100, a weighted-agreement confidence level and per-strategy
    contributions. Includes a track record (hit rate of past outlooks on this
    market, computed from stored daily candles) once enough history has been
    harvested. Ranges: 1d, 1w, 30d, 90d, 180d, 365d.

    ``verbose=false`` omits the chart payload (candles and indicator series)
    and keeps the outlook, signals, values and explanations.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _ANALYSIS_RANGES:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_ANALYSIS_RANGES)}")

    cache_key = f"{market}:{range_}"
    cached = _fable5_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return shape_analysis(cached[1], verbose)

    # Same candle fetch as the analysis endpoint: display window plus
    # warm-up bars so indicators are defined from the first visible bar.
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

    td_context = await get_market_context(market, market_info["asset_class"])
    fable5_context = (
        {
            **td_context,
            "asset_class": market_info["asset_class"],
            "base": market.split("-", 1)[0],
        }
        if td_context is not None
        else None
    )
    result = {
        "market": market,
        "range": range_,
        **fable5_analysis_service.analyze_fable5(candles, display_count, fable5_context),
        "context": serialize_context(td_context),
        "track_record": _fable5_track_record(db, market),
    }
    _fable5_cache[cache_key] = (time.monotonic(), result)
    return shape_analysis(result, verbose)


def _opus_track_record(db: Session, market: str) -> dict | None:
    cached = _opus_track_record_cache.get(market)
    if cached and time.monotonic() - cached[0] < _TRACK_RECORD_TTL:
        return cached[1]
    record = track_record(
        load_recent_daily_candles(db, market),
        opus_analysis_service.analyze_opus,
    )
    _opus_track_record_cache[market] = (time.monotonic(), record)
    return record


@router.get("/{market}/opus-analysis")
async def get_opus_analysis(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range_: Annotated[str, Query(alias="range")] = "30d",
    horizon: str = opus_calibration_service.DEFAULT_HORIZON,
    verbose: bool = True,
):
    """Opus recommendation for one market, with the full feature breakdown.

    Ranks the market against its peer group on the latest completed daily bars,
    scores it with the weights learned for that peer group, horizon and market
    regime, and reports the expected return, the fees it has to clear, the
    resulting conviction and a suggested stop-loss two ATRs below price. Every
    feature is listed with its peer percentile, learned weight, information
    coefficient and contribution, next to the provenance of the calibration
    itself. Includes both a walk-forward track record (replaying this market's
    own history) and the live track record of the recommendations Opus actually
    published. Ranges: 1d, 1w, 30d, 90d, 180d, 365d. Horizons: 1d, 1w, 4w.

    ``verbose=false`` omits the candles of the display window and keeps the
    full feature table, recommendation and track records.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")
    if range_ not in _ANALYSIS_RANGES:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_ANALYSIS_RANGES)}")
    if horizon not in opus_calibration_service.HORIZONS:
        allowed = ", ".join(opus_calibration_service.HORIZONS)
        raise HTTPException(400, f"Invalid horizon: {horizon}. Use one of {allowed}")

    cache_key = f"{market}:{range_}:{horizon}:{user.id}"
    cached = _opus_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CANDLE_TTL:
        return shape_analysis(cached[1], verbose)

    # Same candle fetch as the analysis endpoint: display window plus
    # warm-up bars so indicators are defined from the first visible bar.
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

    scores = await run_in_threadpool(_opus_scores, db)
    context = opus_store.detail_context(scores, market, horizon)
    maker_pct, taker_pct = _opus_fee_rates(db, user)
    if context is not None:
        context = {
            **context,
            "market": market,
            "asset_class": market_info["asset_class"],
            "taker_pct": taker_pct,
            "maker_pct": maker_pct,
        }
    price = market_data_service.get_price(market) or {}
    analysis = opus_analysis_service.analyze_opus(candles, display_count, context)
    row = None
    if context is not None:
        row = opus_analysis_service.finalize_row(
            {
                "market": market,
                "asset_class": market_info["asset_class"],
                "peer_group": context["peer_group"],
                "horizon": horizon,
                "score": analysis["outlook"]["score"],
                "direction": analysis["outlook"]["direction"],
                "expected_return_pct": None if analysis["recommendation"]["expected_return_pct"] is None
                else float(analysis["recommendation"]["expected_return_pct"]),
                "expected_move_pct": context.get("expected_vol_pct"),
                "turnover_eur": context.get("turnover_eur"),
                "days_since_close": context.get("days_since_close"),
            },
            taker_pct=taker_pct,
            maker_pct=maker_pct,
            market_open=price.get("market_open"),
            days_since_close=context.get("days_since_close"),
        )

    result = {
        "market": market,
        "range": range_,
        "horizon": horizon,
        **analysis,
        "cross_section": None if context is None else {
            "peer_group": context["peer_group"],
            "peers": context["peers"],
            "regime": context["regime"],
            "day": context["day"],
            "days_since_close": context["days_since_close"],
        },
        "gates": None if row is None else {
            "liquidity_ok": row["liquidity_ok"],
            "stale": row["stale"],
            "tradable": row["tradable"],
            "tradable_now": row["tradable_now"],
            "low_volatility": row["low_volatility"],
            "suggested_order_type": row["suggested_order_type"],
            "turnover_eur": None if context.get("turnover_eur") is None
            else f"{context['turnover_eur']:.0f}",
        },
        "macro": scores["macro"],
        "track_record": _opus_track_record(db, market),
        "live_track_record": await run_in_threadpool(
            opus_store.live_track_record, db, horizon, market=market
        ),
        "live_track_record_all": await run_in_threadpool(
            opus_store.live_track_record, db, horizon
        ),
    }
    _opus_cache[cache_key] = (time.monotonic(), result)
    return shape_analysis(result, verbose)


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
