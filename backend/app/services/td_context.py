"""Supplementary Twelve Data context for non-crypto analysis engines.

Fetches and caches macro series (VIX, treasury yields), per-stock fundamentals
(earnings proximity, insider activity) and sector-relative performance. Crypto
markets never receive this context.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .instruments import INSTRUMENTS_BY_MARKET
from .twelvedata import TWELVEDATA_REST_URL, twelvedata_service

logger = logging.getLogger("berebank.td_context")

_MACRO_TTL = 900.0
_MARKET_TTL = 3600.0
_EARNINGS_NEAR_DAYS = 5
_INSIDER_LOOKBACK_DAYS = 90

# VIX index is not on all Twelve Data plans; VIXY tracks short-term VIX futures.
_MACRO_VIX_SYMBOLS = ("VIX", "VIXY")
_MACRO_US2Y_SYMBOL = "US2Y"
_MACRO_US10Y_SYMBOLS = ("US10Y", "US30Y")

# S&P 100 base ticker → sector SPDR ETF (XLK already in instruments).
_STOCK_SECTOR_ETF: dict[str, str] = {
    "AAPL": "XLK", "ABBV": "XLV", "ABT": "XLV", "ACN": "XLK", "ADBE": "XLK",
    "AIG": "XLF", "AMD": "XLK", "AMGN": "XLV", "AMT": "XLRE", "AMZN": "XLY",
    "AVGO": "XLK", "AXP": "XLF", "BA": "XLI", "BAC": "XLF", "BK": "XLF",
    "BKNG": "XLY", "BLK": "XLF", "BMY": "XLV", "CHTR": "XLC", "CL": "XLP",
    "CMCSA": "XLC", "COF": "XLF", "COP": "XLE", "COST": "XLP", "CRM": "XLK",
    "CSCO": "XLK", "CVS": "XLV", "DE": "XLI", "DHR": "XLV", "DIS": "XLC",
    "DOW": "XLB", "DUK": "XLU", "EMR": "XLI", "FDX": "XLI", "GD": "XLI",
    "GE": "XLI", "GILD": "XLV", "GM": "XLY", "GOOGL": "XLC", "GS": "XLF",
    "HD": "XLY", "HON": "XLI", "IBM": "XLK", "INTC": "XLK", "INTU": "XLK",
    "ISRG": "XLV", "JNJ": "XLV", "JPM": "XLF", "KHC": "XLP", "KO": "XLP",
    "LIN": "XLB", "LLY": "XLV", "LMT": "XLI", "LOW": "XLY", "MA": "XLF",
    "MCD": "XLY", "MDLZ": "XLP", "MDT": "XLV", "META": "XLC", "MMM": "XLI",
    "MO": "XLP", "MRK": "XLV", "MS": "XLF", "MSFT": "XLK", "NEE": "XLU",
    "NFLX": "XLC", "NKE": "XLY", "NOW": "XLK", "NVDA": "XLK", "ORCL": "XLK",
    "PEP": "XLP", "PFE": "XLV", "PG": "XLP", "PLTR": "XLK", "PM": "XLP",
    "PYPL": "XLF", "QCOM": "XLK", "RTX": "XLI", "SBUX": "XLY", "SCHW": "XLF",
    "SO": "XLU", "SPCX": "XLK", "SPG": "XLRE", "TGT": "XLY", "TMO": "XLV",
    "TMUS": "XLC", "TSLA": "XLY", "TXN": "XLK", "UNH": "XLV", "UNP": "XLI",
    "UPS": "XLI", "USB": "XLF", "V": "XLF", "VZ": "XLC", "WFC": "XLF",
    "WMT": "XLP", "XOM": "XLE",
}

_macro_cache: tuple[float, dict[str, Any]] | None = None
_market_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_macro_lock = asyncio.Lock()


def _day_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def _lookup_series(series: dict[str, float], timestamp_ms: int) -> float | None:
    """Return the value on or before ``timestamp_ms`` (by UTC day)."""
    if not series:
        return None
    key = _day_key(timestamp_ms)
    if key in series:
        return series[key]
    prior = [day for day in series if day <= key]
    return series[max(prior)] if prior else None


def _macro_regime(vix_level: float | None, yield_spread: float | None) -> str:
    if vix_level is not None and vix_level >= 25:
        return "risk_off"
    if yield_spread is not None and yield_spread < 0:
        return "risk_off"
    if (
        vix_level is not None
        and vix_level < 18
        and yield_spread is not None
        and yield_spread > 0.3
    ):
        return "risk_on"
    return "neutral"


def _insider_signal(transactions: list[dict]) -> tuple[str, int, int]:
    """Net insider activity over recent transactions."""
    cutoff = date.today() - timedelta(days=_INSIDER_LOOKBACK_DAYS)
    buys = sells = 0
    for row in transactions:
        raw_date = row.get("date") or row.get("transaction_date") or row.get("datetime") or ""
        try:
            tx_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if tx_date < cutoff:
            continue
        action = str(row.get("action") or row.get("transaction_type") or "").lower()
        if "buy" in action or "purchase" in action:
            buys += 1
        elif "sell" in action or "sale" in action:
            sells += 1
    if buys > sells + 1:
        return "bullish", buys, sells
    if sells > buys + 1:
        return "bearish", buys, sells
    if buys or sells:
        return "neutral", buys, sells
    return "none", buys, sells


def _return_pct(closes: list[tuple[int, float]], bars: int) -> float | None:
    if len(closes) <= bars:
        return None
    start = closes[-bars - 1][1]
    end = closes[-1][1]
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _macro_has_data(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is not None
        for key in ("vix_level", "us2y_yield", "us10y_yield")
    )


def _vix_proxy_level(rows: list[tuple[int, float]]) -> float | None:
    """Map VIXY prices onto a VIX-like scale using recent z-score."""
    if not rows:
        return None
    current = rows[-1][1]
    if len(rows) < 20:
        return current
    window = [close for _, close in rows[-60:]]
    mean = statistics.fmean(window)
    stdev = statistics.pstdev(window) or 1.0
    z = (current - mean) / stdev
    return max(10.0, min(40.0, 20.0 + z * 5.0))


def _vix_proxy_by_day(rows: list[tuple[int, float]]) -> dict[str, float]:
    if not rows:
        return {}
    closes = [close for _, close in rows]
    mean = statistics.fmean(closes)
    stdev = statistics.pstdev(closes) or 1.0
    by_day: dict[str, float] = {}
    for ts, close in rows:
        z = (close - mean) / stdev
        by_day[_day_key(ts)] = max(10.0, min(40.0, 20.0 + z * 5.0))
    return by_day


async def _us10y_symbol_candidates(client: httpx.AsyncClient) -> list[str]:
    """Prefer US 10Y tickers from the bonds catalog when the plan exposes them."""
    candidates = list(_MACRO_US10Y_SYMBOLS)
    bond_symbols = await twelvedata_service.fetch_bond_symbols(client=client)
    for symbol in bond_symbols:
        upper = symbol.upper()
        if upper.startswith("US") and "10" in upper and symbol not in candidates:
            candidates.insert(0, symbol)
    return candidates


async def _fetch_macro(client: httpx.AsyncClient) -> dict[str, Any]:
    us10y_candidates = await _us10y_symbol_candidates(client)
    (vix_symbol, vix_rows), us2y_rows, (us10y_symbol, us10y_rows) = await asyncio.gather(
        twelvedata_service.fetch_first_time_series(
            list(_MACRO_VIX_SYMBOLS), outputsize=260, client=client
        ),
        twelvedata_service.fetch_symbol_time_series(
            _MACRO_US2Y_SYMBOL, outputsize=260, client=client
        ),
        twelvedata_service.fetch_first_time_series(
            us10y_candidates, outputsize=260, client=client
        ),
    )
    if vix_symbol and vix_symbol != _MACRO_VIX_SYMBOLS[0]:
        logger.info("Twelve Data macro VIX: using fallback symbol %s", vix_symbol)
    if us10y_symbol and us10y_symbol not in _MACRO_US10Y_SYMBOLS[:1]:
        logger.info("Twelve Data macro US10Y: using symbol %s", us10y_symbol)

    vix_proxy = vix_symbol is not None and vix_symbol != "VIX"
    if vix_proxy:
        vix_by_day = _vix_proxy_by_day(vix_rows)
        vix_level = _vix_proxy_level(vix_rows)
    else:
        vix_by_day = {_day_key(ts): close for ts, close in vix_rows}
        vix_level = vix_rows[-1][1] if vix_rows else None

    us2y_by_day = {_day_key(ts): close for ts, close in us2y_rows}
    us10y_by_day = {_day_key(ts): close for ts, close in us10y_rows}
    spread_by_day: dict[str, float] = {}
    for day in sorted(set(us2y_by_day) & set(us10y_by_day)):
        spread_by_day[day] = us10y_by_day[day] - us2y_by_day[day]

    us2y = us2y_rows[-1][1] if us2y_rows else None
    us10y = us10y_rows[-1][1] if us10y_rows else None
    yield_spread = (us10y - us2y) if us2y is not None and us10y is not None else None
    vix_change_pct = None
    if len(vix_rows) > 5 and vix_rows[-6][1] > 0:
        vix_change_pct = (vix_rows[-1][1] / vix_rows[-6][1] - 1.0) * 100.0

    return {
        "vix_level": vix_level,
        "vix_change_pct": vix_change_pct,
        "us2y_yield": us2y,
        "us10y_yield": us10y,
        "yield_spread": yield_spread,
        "macro_regime": _macro_regime(vix_level, yield_spread),
        "vix_by_day": vix_by_day,
        "yield_spread_by_day": spread_by_day,
    }


async def get_macro_context() -> dict[str, Any] | None:
    """Shared VIX / treasury context for stocks and funds."""
    global _macro_cache
    if twelvedata_service.api_key is None:
        return None
    now = time.monotonic()
    if _macro_cache and now - _macro_cache[0] < _MACRO_TTL:
        return _macro_cache[1]
    async with _macro_lock:
        now = time.monotonic()
        if _macro_cache and now - _macro_cache[0] < _MACRO_TTL:
            return _macro_cache[1]
        try:
            async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as client:
                payload = await _fetch_macro(client)
        except Exception:
            logger.exception("Twelve Data macro fetch failed")
            return _macro_cache[1] if _macro_cache else None
        if _macro_has_data(payload):
            _macro_cache = (now, payload)
            return payload
        return _macro_cache[1] if _macro_cache else payload


async def _fetch_market_context(
    market: str,
    asset_class: str,
    macro: dict[str, Any],
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    inst = INSTRUMENTS_BY_MARKET.get(market)
    context: dict[str, Any] = {
        "macro_regime": macro.get("macro_regime", "neutral"),
        "vix_level": macro.get("vix_level"),
        "vix_change_pct": macro.get("vix_change_pct"),
        "us2y_yield": macro.get("us2y_yield"),
        "us10y_yield": macro.get("us10y_yield"),
        "yield_spread": macro.get("yield_spread"),
        "vix_by_day": macro.get("vix_by_day", {}),
        "yield_spread_by_day": macro.get("yield_spread_by_day", {}),
        "days_to_earnings": None,
        "earnings_near": False,
        "insider_signal": "none",
        "insider_buys": 0,
        "insider_sells": 0,
        "sector_etf": None,
        "sector_relative_return": None,
    }
    if asset_class != "stock" or inst is None:
        return context

    symbol = inst.symbol
    sector = _STOCK_SECTOR_ETF.get(symbol)
    context["sector_etf"] = sector

    earnings_task = twelvedata_service.fetch_next_earnings(symbol, client=client)
    insider_task = twelvedata_service.fetch_insider_transactions(symbol, client=client)
    sector_market = None
    if sector:
        candidate = f"{sector}-EUR"
        if candidate in INSTRUMENTS_BY_MARKET:
            sector_market = candidate

    earnings, insiders = await asyncio.gather(
        earnings_task,
        insider_task,
        return_exceptions=True,
    )
    if isinstance(earnings, dict):
        raw_date = earnings.get("date") or earnings.get("report_date") or earnings.get("datetime")
        if raw_date:
            try:
                report = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
                days = (report - date.today()).days
                context["days_to_earnings"] = days
                context["earnings_near"] = 0 <= days <= _EARNINGS_NEAR_DAYS
            except ValueError:
                pass

    if isinstance(insiders, list):
        signal, buys, sells = _insider_signal(insiders)
        context["insider_signal"] = signal
        context["insider_buys"] = buys
        context["insider_sells"] = sells

    if sector_market is not None:
        try:
            stock_rows, sector_rows = await asyncio.gather(
                twelvedata_service.fetch_candles(market, "30d", extra_bars=10),
                twelvedata_service.fetch_candles(sector_market, "30d", extra_bars=10),
            )
            stock_closes = [(int(r[0]), float(r[4])) for r in stock_rows]
            sector_closes = [(int(r[0]), float(r[4])) for r in sector_rows]
            stock_ret = _return_pct(stock_closes, 20)
            sector_ret = _return_pct(sector_closes, 20)
            if stock_ret is not None and sector_ret is not None:
                context["sector_relative_return"] = stock_ret - sector_ret
        except Exception:
            pass

    return context


async def get_market_context(market: str, asset_class: str) -> dict[str, Any] | None:
    """Full supplementary context for a non-crypto market."""
    if asset_class == "crypto" or twelvedata_service.api_key is None:
        return None
    macro = await get_macro_context()
    if macro is None:
        return None

    now = time.monotonic()
    cached = _market_cache.get(market)
    if cached and now - cached[0] < _MARKET_TTL:
        merged = {**macro, **{k: v for k, v in cached[1].items() if k not in macro}}
        return merged

    try:
        async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as client:
            market_part = await _fetch_market_context(market, asset_class, macro, client)
    except Exception:
        return macro if asset_class != "stock" else cached[1] if cached else macro

    _market_cache[market] = (now, market_part)
    return {**macro, **{k: v for k, v in market_part.items() if k not in macro}}


def serialize_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """API-safe subset without internal series maps."""
    if not context:
        return None
    return {
        "macro_regime": context.get("macro_regime"),
        "vix_level": _fmt(context.get("vix_level")),
        "vix_change_pct": _fmt(context.get("vix_change_pct")),
        "us2y_yield": _fmt(context.get("us2y_yield")),
        "us10y_yield": _fmt(context.get("us10y_yield")),
        "yield_spread": _fmt(context.get("yield_spread")),
        "days_to_earnings": context.get("days_to_earnings"),
        "earnings_near": bool(context.get("earnings_near")),
        "insider_signal": context.get("insider_signal"),
        "insider_buys": context.get("insider_buys", 0),
        "insider_sells": context.get("insider_sells", 0),
        "sector_etf": context.get("sector_etf"),
        "sector_relative_return": _fmt(context.get("sector_relative_return")),
    }


def macro_features_at(
    context: dict[str, Any] | None,
    timestamp_ms: int,
    *,
    current_only: bool = False,
) -> dict[str, float | None]:
    """Normalized macro/event features for GTP56Sol snapshots."""
    if not context:
        return {
            "vix_normalized": None,
            "yield_spread": None,
            "earnings_proximity": None,
            "insider_activity": None,
        }
    if current_only:
        vix = context.get("vix_level")
        spread = context.get("yield_spread")
        earnings = 1.0 if context.get("earnings_near") else 0.0
        insider = context.get("insider_signal", "none")
    else:
        vix = _lookup_series(context.get("vix_by_day", {}), timestamp_ms)
        spread = _lookup_series(context.get("yield_spread_by_day", {}), timestamp_ms)
        earnings = 0.0
        insider = "none"

    insider_activity = (
        1.0 if insider == "bullish" else -1.0 if insider == "bearish" else 0.0
    )
    vix_normalized = None
    if vix is not None:
        vix_normalized = max(-3.0, min(3.0, (vix - 20.0) / 10.0))
    return {
        "vix_normalized": vix_normalized,
        "yield_spread": spread,
        "earnings_proximity": earnings,
        "insider_activity": insider_activity if current_only else 0.0,
    }


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
