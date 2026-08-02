"""Supplementary free-API context for crypto analysis engines.

Fetches and caches macro series (Fear & Greed, BTC dominance, stablecoin
supply) and per-market BTC correlation from Bitvavo candles. Live fetches are
cached in memory; nothing is persisted to the database (see module note on
import/export at ``get_market_context``).

Persistence/backfill: candle import/export covers OHLCV only. Macro series are
re-fetched from free APIs on demand — Alternative.me exposes FNG history inline,
while CoinGecko global dominance is current-only on the free tier, so GTP56Sol
historical macro features use FNG/stablecoin series where available and leave
dominance null on past bars.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import BITVAVO_REST_URL
from .coinglass import coinglass_service, get_symbol_derivatives

logger = logging.getLogger("berebank.crypto_context")

_MACRO_TTL = 900.0
_MARKET_TTL = 3600.0
_CORRELATION_BARS = 30
_BTC_MARKET = "BTC-EUR"

_FNG_URL = "https://api.alternative.me/fng/"
_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_DEFILLAMA_STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoincharts/all"

_macro_cache: tuple[float, dict[str, Any]] | None = None
_market_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_macro_lock = asyncio.Lock()


def _day_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def _lookup_series(series: dict[str, float], timestamp_ms: int) -> float | None:
    if not series:
        return None
    key = _day_key(timestamp_ms)
    if key in series:
        return series[key]
    prior = [day for day in series if day <= key]
    return series[max(prior)] if prior else None


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0:
        return "0"
    text = f"{number:.10g}"
    if "e" in text or "E" in text:
        text = f"{number:.10f}".rstrip("0").rstrip(".")
    return text


def _macro_regime(
    fear_greed: float | None,
    dominance_change_pct: float | None,
    stablecoin_change_pct: float | None,
) -> str:
    if fear_greed is not None and fear_greed <= 25:
        return "risk_on"
    if fear_greed is not None and fear_greed >= 75:
        return "risk_off"
    if dominance_change_pct is not None and dominance_change_pct > 1.0:
        return "risk_off"
    if stablecoin_change_pct is not None and stablecoin_change_pct < -3.0:
        return "risk_off"
    if (
        fear_greed is not None
        and 45 <= fear_greed <= 65
        and (stablecoin_change_pct is None or stablecoin_change_pct > 0)
    ):
        return "risk_on"
    return "neutral"


def _pct_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current / prior - 1.0) * 100.0


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 10 or len(xs) != len(ys):
        return None
    if statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (
        sum((x - mx) ** 2 for x in xs) ** 0.5
        * sum((y - my) ** 2 for y in ys) ** 0.5
    )
    if den == 0:
        return None
    return max(-1.0, min(1.0, num / den))


def _daily_returns(candles: list[list]) -> dict[str, float]:
    """Map UTC day -> daily close-to-close return percent."""
    if len(candles) < 2:
        return {}
    sorted_candles = sorted(candles, key=lambda row: int(row[0]))
    by_day: dict[str, float] = {}
    prev_close: float | None = None
    prev_day: str | None = None
    for row in sorted_candles:
        day = _day_key(int(row[0]))
        close = float(row[4])
        if prev_close is not None and prev_day is not None and day != prev_day:
            if prev_close > 0:
                by_day[day] = (close / prev_close - 1.0) * 100.0
        prev_close = close
        prev_day = day
    return by_day


def btc_correlation(btc_candles: list[list], market_candles: list[list]) -> float | None:
    """Pearson correlation of daily returns vs BTC-EUR over recent bars."""
    if not btc_candles or not market_candles:
        return None
    btc_returns = _daily_returns(btc_candles)
    market_returns = _daily_returns(market_candles)
    shared_days = sorted(set(btc_returns) & set(market_returns))[-_CORRELATION_BARS:]
    if len(shared_days) < 10:
        return None
    xs = [btc_returns[day] for day in shared_days]
    ys = [market_returns[day] for day in shared_days]
    return _pearson(xs, ys)


def _macro_has_data(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is not None
        for key in ("fear_greed_index", "btc_dominance", "stablecoin_supply_usd")
    )


async def _fetch_bitvavo_candles(
    client: httpx.AsyncClient,
    market: str,
    *,
    limit: int = _CORRELATION_BARS + 5,
) -> list[list]:
    resp = await client.get(
        f"/{market}/candles",
        params={"interval": "1d", "limit": limit},
    )
    if resp.status_code != 200:
        return []
    return sorted(resp.json(), key=lambda row: row[0])


async def _fetch_fear_greed(client: httpx.AsyncClient) -> tuple[dict[str, float], dict[str, Any]]:
    resp = await client.get(_FNG_URL, params={"limit": 365, "format": "json"})
    resp.raise_for_status()
    rows = resp.json().get("data") or []
    by_day: dict[str, float] = {}
    latest: dict[str, Any] = {}
    for row in rows:
        try:
            ts = int(row["timestamp"]) * 1000
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        day = _day_key(ts)
        by_day[day] = value
        if not latest:
            latest = {
                "fear_greed_index": int(round(value)),
                "fear_greed_classification": row.get("value_classification"),
            }
    if latest and len(rows) >= 8:
        try:
            current = float(rows[0]["value"])
            week_ago = float(rows[7]["value"])
            latest["fear_greed_change"] = current - week_ago
        except (KeyError, TypeError, ValueError):
            pass
    return by_day, latest


async def _fetch_btc_dominance(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.get(_COINGECKO_GLOBAL_URL)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    pct = data.get("market_cap_percentage") or {}
    dominance = pct.get("btc")
    if dominance is None:
        return {}
    dominance_f = float(dominance)
    return {
        "btc_dominance": dominance_f,
        "btc_dominance_by_day": {_day_key(int(time.time() * 1000)): dominance_f},
    }


async def _fetch_stablecoin_supply(client: httpx.AsyncClient) -> tuple[dict[str, float], dict[str, Any]]:
    resp = await client.get(_DEFILLAMA_STABLECOINS_URL)
    resp.raise_for_status()
    rows = resp.json() or []
    by_day: dict[str, float] = {}
    for row in rows:
        try:
            ts = int(row["date"]) * 1000
            total = row.get("totalCirculating") or {}
            usd = total.get("peggedUSD")
            if usd is None:
                continue
            by_day[_day_key(ts)] = float(usd)
        except (KeyError, TypeError, ValueError):
            continue
    if not by_day:
        return {}, {}
    days = sorted(by_day)
    current = by_day[days[-1]]
    month_ago = by_day.get(days[max(0, len(days) - 31)])
    return by_day, {
        "stablecoin_supply_usd": current,
        "stablecoin_supply_change_pct": _pct_change(current, month_ago),
    }


async def _fetch_macro(client: httpx.AsyncClient) -> dict[str, Any]:
    (
        (fear_by_day, fear_latest),
        dominance_latest,
        (stable_by_day, stable_latest),
    ) = await asyncio.gather(
        _fetch_fear_greed(client),
        _fetch_btc_dominance(client),
        _fetch_stablecoin_supply(client),
        return_exceptions=True,
    )

    payload: dict[str, Any] = {
        "context_type": "crypto",
        "fear_greed_by_day": {},
        "btc_dominance_by_day": {},
        "stablecoin_supply_by_day": {},
        "fear_greed_index": None,
        "fear_greed_classification": None,
        "fear_greed_change": None,
        "btc_dominance": None,
        "btc_dominance_change_pct": None,
        "stablecoin_supply_usd": None,
        "stablecoin_supply_change_pct": None,
    }

    if isinstance(fear_by_day, dict):
        payload["fear_greed_by_day"] = fear_by_day
        payload.update({k: v for k, v in (fear_latest or {}).items() if v is not None})
    elif isinstance(fear_by_day, Exception):
        logger.warning("Fear & Greed fetch failed: %s", fear_by_day)

    if isinstance(dominance_latest, dict):
        payload.update({k: v for k, v in dominance_latest.items() if k != "btc_dominance_by_day"})
        payload["btc_dominance_by_day"] = dominance_latest.get("btc_dominance_by_day", {})
    elif isinstance(dominance_latest, Exception):
        logger.warning("BTC dominance fetch failed: %s", dominance_latest)

    if isinstance(stable_by_day, dict):
        payload["stablecoin_supply_by_day"] = stable_by_day
        payload.update({k: v for k, v in (stable_latest or {}).items() if v is not None})
    elif isinstance(stable_by_day, Exception):
        logger.warning("Stablecoin supply fetch failed: %s", stable_by_day)

    dom_days = sorted(payload["btc_dominance_by_day"])
    if len(dom_days) >= 2:
        current_dom = payload["btc_dominance_by_day"][dom_days[-1]]
        prior_dom = payload["btc_dominance_by_day"][dom_days[-2]]
        payload["btc_dominance_change_pct"] = _pct_change(current_dom, prior_dom)

    payload["macro_regime"] = _macro_regime(
        payload.get("fear_greed_index"),
        payload.get("btc_dominance_change_pct"),
        payload.get("stablecoin_supply_change_pct"),
    )
    return payload


async def get_macro_context() -> dict[str, Any] | None:
    """Shared crypto macro context (Fear & Greed, dominance, stablecoins)."""
    global _macro_cache
    now = time.monotonic()
    if _macro_cache and now - _macro_cache[0] < _MACRO_TTL:
        return _macro_cache[1]
    async with _macro_lock:
        now = time.monotonic()
        if _macro_cache and now - _macro_cache[0] < _MACRO_TTL:
            return _macro_cache[1]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                payload = await _fetch_macro(client)
        except Exception:
            logger.exception("Crypto macro fetch failed")
            return _macro_cache[1] if _macro_cache else None
        if _macro_has_data(payload):
            _macro_cache = (now, payload)
            return payload
        return _macro_cache[1] if _macro_cache else payload


async def _fetch_market_part(
    market: str,
    macro: dict[str, Any],
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    context = {**macro, "btc_correlation": None}
    if market == _BTC_MARKET:
        context["btc_correlation"] = 1.0
        return context
    try:
        async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as bv:
            btc_candles, market_candles = await asyncio.gather(
                _fetch_bitvavo_candles(bv, _BTC_MARKET),
                _fetch_bitvavo_candles(bv, market),
            )
        context["btc_correlation"] = btc_correlation(btc_candles, market_candles)
    except Exception:
        logger.debug("BTC correlation fetch failed for %s", market, exc_info=True)
    return context


def _market_base(market: str) -> str:
    return market.split("-", 1)[0].upper()


async def _fetch_derivatives(base: str) -> dict[str, Any]:
    if not coinglass_service.api_key:
        return {}
    try:
        return await get_symbol_derivatives(base)
    except Exception:
        logger.debug("Coinglass derivatives fetch failed for %s", base, exc_info=True)
        return {}


async def get_market_context(market: str) -> dict[str, Any] | None:
    """Full supplementary context for a crypto market."""
    macro = await get_macro_context()
    if macro is None:
        return None

    base = _market_base(market)
    now = time.monotonic()
    cached = _market_cache.get(market)
    if cached and now - cached[0] < _MARKET_TTL:
        return {
            **macro,
            **cached[1],
        }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            market_part = await _fetch_market_part(market, macro, client)
    except Exception:
        if cached:
            return {**macro, **cached[1]}
        return macro

    derivatives = await _fetch_derivatives(base)
    if derivatives:
        market_part.update(derivatives)

    cache_part = {
        "btc_correlation": market_part.get("btc_correlation"),
        **{k: v for k, v in derivatives.items() if k != "coinglass_symbol"},
    }
    _market_cache[market] = (now, cache_part)
    return market_part


def serialize_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context or context.get("context_type") != "crypto":
        return None
    return {
        "context_type": "crypto",
        "macro_regime": context.get("macro_regime"),
        "fear_greed_index": context.get("fear_greed_index"),
        "fear_greed_classification": context.get("fear_greed_classification"),
        "fear_greed_change": _fmt(context.get("fear_greed_change")),
        "btc_dominance": _fmt(context.get("btc_dominance")),
        "btc_dominance_change_pct": _fmt(context.get("btc_dominance_change_pct")),
        "btc_correlation": _fmt(context.get("btc_correlation")),
        "stablecoin_supply_usd": _fmt(context.get("stablecoin_supply_usd")),
        "stablecoin_supply_change_pct": _fmt(context.get("stablecoin_supply_change_pct")),
        "funding_rate_avg": _fmt(context.get("funding_rate_avg")),
        "funding_rate_change_24h": _fmt(context.get("funding_rate_change_24h")),
        "open_interest_change_percent_24h": _fmt(context.get("open_interest_change_percent_24h")),
        "open_interest_change_percent_4h": _fmt(context.get("open_interest_change_percent_4h")),
        "open_interest_change_percent_1h": _fmt(context.get("open_interest_change_percent_1h")),
        "open_interest_usd": _fmt(context.get("open_interest_usd")),
        "long_short_ratio": _fmt(context.get("long_short_ratio")),
        "long_liquidation_usd_24h": _fmt(context.get("long_liquidation_usd_24h")),
        "short_liquidation_usd_24h": _fmt(context.get("short_liquidation_usd_24h")),
    }


def macro_features_at(
    context: dict[str, Any] | None,
    timestamp_ms: int,
    *,
    current_only: bool = False,
) -> dict[str, float | None]:
    """Map crypto macro data onto GTP56Sol's four macro feature slots."""
    if not context or context.get("context_type") != "crypto":
        return {
            "vix_normalized": None,
            "yield_spread": None,
            "earnings_proximity": None,
            "insider_activity": None,
            "funding_normalized": None,
            "oi_change_24h": None,
        }

    if current_only:
        fg = context.get("fear_greed_index")
        dominance = context.get("btc_dominance")
        correlation = context.get("btc_correlation")
        stable_change = context.get("stablecoin_supply_change_pct")
    else:
        fg = _lookup_series(context.get("fear_greed_by_day", {}), timestamp_ms)
        dominance = _lookup_series(context.get("btc_dominance_by_day", {}), timestamp_ms)
        correlation = context.get("btc_correlation") if current_only else None
        day = _day_key(timestamp_ms)
        stable_series = context.get("stablecoin_supply_by_day", {})
        stable_current = _lookup_series(stable_series, timestamp_ms)
        prior_day = max((d for d in stable_series if d < day), default=None)
        stable_change = (
            _pct_change(stable_current, stable_series[prior_day])
            if stable_current is not None and prior_day
            else None
        )

    fear_greed_normalized = None
    if fg is not None:
        fear_greed_normalized = max(-3.0, min(3.0, (float(fg) - 50.0) / 16.0))

    dominance_centered = None
    if dominance is not None:
        dominance_centered = float(dominance) - 50.0

    correlation_feature = float(correlation) if correlation is not None else None

    stable_activity = None
    if stable_change is not None:
        stable_activity = max(-1.0, min(1.0, float(stable_change) / 5.0))

    funding = context.get("funding_rate_avg") if current_only else None
    funding_normalized = None
    if funding is not None:
        # Coinglass rates are percent points; scale ~±0.05% to roughly ±1.
        funding_normalized = max(-3.0, min(3.0, float(funding) / 0.05))

    oi_change = context.get("open_interest_change_percent_24h") if current_only else None
    oi_change_feature = None
    if oi_change is not None:
        oi_change_feature = max(-3.0, min(3.0, float(oi_change) / 10.0))

    return {
        "vix_normalized": fear_greed_normalized,
        "yield_spread": dominance_centered,
        "earnings_proximity": correlation_feature,
        "insider_activity": stable_activity if current_only else stable_activity,
        "funding_normalized": funding_normalized,
        "oi_change_24h": oi_change_feature,
    }
