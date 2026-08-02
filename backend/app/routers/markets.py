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
from ..services.backtest import track_record
from ..services.candle_store import (
    CandleHistorySummary,
    completed_history_summaries,
    completed_history_summary,
    ensure_gtp56sol_deep_history,
    load_completed_daily_candles,
    load_recent_daily_candles,
)
from ..services.market_data import market_data_service
from ..services.rss_aggregator import fetch_articles_for_market, get_markets_with_articles, merge_news_items
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
_news_cache: dict[str, tuple[float, list]] = {}
_CANDLE_TTL = 60  # seconds
_GTP56SOL_TTL = 3600  # daily stored inputs change slowly
_GTP56SOL_PEER_CAP = 8
_GTP56SOL_MAX_CPU_FORECASTS = 2
_TECHNICAL_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_KIMI_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_FABLE5_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_GTP56SOL_OUTLOOKS_TTL = 900  # seconds; computed from daily candles, which change slowly
_GTP56SOL_OUTLOOKS_WORKERS = 4
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
def get_kimi_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """KimiK3 direction outlook summary for every market, for list views.

    Computed from the stored daily candles (harvested in the background),
    so a market appears once enough history has been collected. Values:
    direction, score (-100..+100), confidence and regime — same engine as
    the per-market KimiK3 analysis endpoint.
    """
    global _kimi_outlooks_cache
    if _kimi_outlooks_cache and time.monotonic() - _kimi_outlooks_cache[0] < _KIMI_OUTLOOKS_TTL:
        return _kimi_outlooks_cache[1]

    outlooks: dict[str, dict] = {}
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) < 60:
            continue
        outlook = kimi_analysis_service.analyze_kimi(candles, len(candles))["outlook"]
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
    _kimi_outlooks_cache = (time.monotonic(), result)
    return result


@router.get("/fable5-outlooks")
def get_fable5_outlooks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fable5 direction outlook summary for every market, for list views.

    Computed from the stored daily candles (harvested in the background),
    so a market appears once enough history has been collected. Values:
    direction, score (-100..+100), confidence and regime — same engine as
    the per-market Fable5 analysis endpoint.
    """
    global _fable5_outlooks_cache
    if _fable5_outlooks_cache and time.monotonic() - _fable5_outlooks_cache[0] < _FABLE5_OUTLOOKS_TTL:
        return _fable5_outlooks_cache[1]

    outlooks: dict[str, dict] = {}
    for market in market_data_service.markets:
        candles = load_recent_daily_candles(db, market)
        if len(candles) < 60:
            continue
        outlook = fable5_analysis_service.analyze_fable5(candles, len(candles))["outlook"]
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
    _fable5_outlooks_cache = (time.monotonic(), result)
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
) -> dict:
    """Run pure forecast work off-loop with bounded process concurrency."""
    forecast_func = forecast_func or gtp56sol_analysis_service.forecast
    async with _gtp56sol_cpu_semaphore():
        return await run_in_threadpool(
            forecast_func,
            candles,
            horizon,
            fallback_candles_by_market=fallback,
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

            forecast_payload = await _run_gtp56sol_forecast(
                candles,
                horizon,
                fallback,
            )
            result = {
                "market": market,
                "asset_class": market_info["asset_class"],
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
):
    """KimiK3 direction outlook for a market over the requested range.

    Blends the five analysis strategies plus an ADX trend-strength signal
    into a single outlook: direction (bullish/bearish/neutral), a score
    from -100 to +100, a confidence level and per-strategy contributions.
    Includes a track record (hit rate of past outlooks on this market,
    computed from stored daily candles) once enough history has been
    harvested. Ranges: 1d, 1w, 30d, 90d, 180d, 365d.
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
        return cached[1]

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

    result = {
        "market": market,
        "range": range_,
        **kimi_analysis_service.analyze_kimi(candles, display_count),
        "track_record": _kimi_track_record(db, market),
    }
    _kimi_cache[cache_key] = (time.monotonic(), result)
    return result


@router.get("/{market}/fable5-analysis")
async def get_fable5_analysis(
    market: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    range_: Annotated[str, Query(alias="range")] = "30d",
):
    """Fable5 direction outlook for a market over the requested range.

    Blends the five analysis strategies plus dual-horizon momentum, a slow
    stochastic oscillator and ADX trend strength — eight signals with fixed
    importance weights — into a single outlook: direction
    (bullish/bearish/neutral), a score from -100 to +100, a weighted-agreement
    confidence level and per-strategy contributions. Includes a track record
    (hit rate of past outlooks on this market, computed from stored daily
    candles) once enough history has been harvested.
    Ranges: 1d, 1w, 30d, 90d, 180d, 365d.
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
        return cached[1]

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

    result = {
        "market": market,
        "range": range_,
        **fable5_analysis_service.analyze_fable5(candles, display_count),
        "track_record": _fable5_track_record(db, market),
    }
    _fable5_cache[cache_key] = (time.monotonic(), result)
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
