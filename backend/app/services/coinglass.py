"""Coinglass derivatives context for crypto analysis engines.

Uses the Hobbyist-friendly bulk funding endpoint plus per-symbol open-interest
aggregates. Results are cached in memory; nothing is persisted to the database.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("berebank.coinglass")

COINGLASS_REST_URL = "https://open-api-v4.coinglass.com"
_CACHE_TTL = 900.0
_OI_CACHE_TTL = 900.0
_PAIRS_CACHE_TTL = 900.0
_FUNDING_HIST_CACHE_TTL = 900.0

# The funding history endpoint requires naming one exchange; Binance runs
# the largest perpetuals market, so its funding trend is the reference.
_FUNDING_REFERENCE_EXCHANGE = "Binance"

_funding_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
_oi_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_pairs_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_funding_hist_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_funding_lock = asyncio.Lock()
_oi_lock = asyncio.Lock()
_pairs_lock = asyncio.Lock()
_funding_hist_lock = asyncio.Lock()


class CoinglassService:
    def __init__(self) -> None:
        self.api_key: str | None = None
        self.last_error: str | None = None
        self.last_update: float | None = None

    def set_api_key(self, api_key: str | None) -> None:
        self.api_key = api_key.strip() if api_key else None
        global _funding_cache, _oi_cache, _pairs_cache, _funding_hist_cache
        _funding_cache = None
        _oi_cache = {}
        _pairs_cache = {}
        _funding_hist_cache = {}

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.api_key is not None,
            "last_update": self.last_update,
            "error": self.last_error,
        }


coinglass_service = CoinglassService()


def _headers() -> dict[str, str]:
    if not coinglass_service.api_key:
        raise RuntimeError("Coinglass API key not configured")
    return {"CG-API-KEY": coinglass_service.api_key, "accept": "application/json"}


def _avg_funding(row: dict[str, Any]) -> float | None:
    rates: list[float] = []
    for key in ("stablecoin_margin_list", "token_margin_list"):
        for item in row.get(key) or []:
            rate = item.get("funding_rate")
            if rate is not None:
                try:
                    rates.append(float(rate))
                except (TypeError, ValueError):
                    continue
    if not rates:
        return None
    return sum(rates) / len(rates)


def resolve_coinglass_symbol(base: str, funding_map: dict[str, Any]) -> str | None:
    """Map a Bitvavo base asset to a Coinglass futures symbol."""
    symbol = base.upper()
    if symbol in funding_map:
        return symbol
    for prefix in ("1000", "10000", "1000000"):
        candidate = f"{prefix}{symbol}"
        if candidate in funding_map:
            return candidate
    return None


async def _get_json(client: httpx.AsyncClient, path: str, *, params: dict | None = None) -> Any:
    resp = await client.get(path, params=params or {})
    if resp.status_code == 429:
        logger.warning("Coinglass rate limit on %s", path)
        return None
    resp.raise_for_status()
    body = resp.json()
    if str(body.get("code")) not in ("0", "200"):
        msg = body.get("msg") or body.get("message") or body.get("code")
        logger.warning("Coinglass %s returned code %s: %s", path, body.get("code"), msg)
        return None
    return body.get("data")


async def fetch_all_funding(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    rows = await _get_json(client, "/api/futures/funding-rate/exchange-list")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if not symbol:
            continue
        avg = _avg_funding(row)
        if avg is None:
            continue
        out[str(symbol).upper()] = {
            "funding_rate_avg": avg,
            "exchange_count": len(row.get("stablecoin_margin_list") or [])
            + len(row.get("token_margin_list") or []),
        }
    return out


async def fetch_open_interest(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    rows = await _get_json(client, "/api/futures/open-interest/exchange-list", params={"symbol": symbol})
    if not isinstance(rows, list):
        return None
    aggregate = next((row for row in rows if row.get("exchange") == "All"), None)
    if aggregate is None and rows:
        aggregate = rows[0]
    if not aggregate:
        return None
    return {
        "open_interest_usd": aggregate.get("open_interest_usd"),
        "open_interest_change_percent_24h": aggregate.get("open_interest_change_percent_24h"),
        "open_interest_change_percent_4h": aggregate.get("open_interest_change_percent_4h"),
        "open_interest_change_percent_1h": aggregate.get("open_interest_change_percent_1h"),
    }


def _sum_field(rows: list[dict], field: str) -> float | None:
    total = 0.0
    seen = False
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        try:
            total += float(value)
            seen = True
        except (TypeError, ValueError):
            continue
    return total if seen else None


def aggregate_pairs_markets(rows: list[dict]) -> dict[str, Any]:
    """Cross-exchange positioning aggregates from ``/pairs-markets`` rows.

    Long/short taker volume ratio and 24h liquidation split — the short-term
    positioning data the Hobbyist plan exposes per coin in one call.
    """
    long_vol = _sum_field(rows, "long_volume_usd")
    short_vol = _sum_field(rows, "short_volume_usd")
    long_liq = _sum_field(rows, "long_liquidation_usd_24h")
    short_liq = _sum_field(rows, "short_liquidation_usd_24h")
    out: dict[str, Any] = {}
    if long_vol is not None and short_vol is not None and short_vol > 0:
        out["long_short_ratio"] = long_vol / short_vol
    if long_liq is not None or short_liq is not None:
        out["long_liquidation_usd_24h"] = long_liq or 0.0
        out["short_liquidation_usd_24h"] = short_liq or 0.0
    return out


async def fetch_pairs_markets(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    rows = await _get_json(client, "/api/futures/pairs-markets", params={"symbol": symbol})
    if not isinstance(rows, list) or not rows:
        return None
    return aggregate_pairs_markets(rows)


def _funding_close(row: dict[str, Any]) -> float | None:
    value = row.get("c", row.get("close"))
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_funding_history(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    """24h funding-rate trend from 4h funding OHLC (Hobbyist: interval >= 4h).

    Returns the change in percentage points between the oldest and newest
    close over roughly the past day, plus the reference exchange used.
    """
    rows = await _get_json(
        client,
        "/api/futures/funding-rate/history",
        params={
            "exchange": _FUNDING_REFERENCE_EXCHANGE,
            "symbol": symbol,
            "interval": "4h",
            "limit": 7,
        },
    )
    if not isinstance(rows, list) or len(rows) < 2:
        return None

    def _row_ts(row: dict[str, Any]) -> int:
        try:
            return int(row.get("t", row.get("time")) or 0)
        except (TypeError, ValueError):
            return 0

    closes = [c for c in (_funding_close(row) for row in sorted(rows, key=_row_ts)) if c is not None]
    if len(closes) < 2:
        return None
    return {
        "funding_rate_change_24h": closes[-1] - closes[0],
        "funding_rate_reference_exchange": _FUNDING_REFERENCE_EXCHANGE,
    }


async def get_funding_map() -> dict[str, dict[str, Any]]:
    """All symbols' average funding rates (one Coinglass call, cached)."""
    global _funding_cache
    if not coinglass_service.api_key:
        return {}
    now = time.monotonic()
    if _funding_cache and now - _funding_cache[0] < _CACHE_TTL:
        return _funding_cache[1]
    async with _funding_lock:
        now = time.monotonic()
        if _funding_cache and now - _funding_cache[0] < _CACHE_TTL:
            return _funding_cache[1]
        try:
            async with httpx.AsyncClient(base_url=COINGLASS_REST_URL, headers=_headers(), timeout=45) as client:
                payload = await fetch_all_funding(client)
        except Exception:
            logger.exception("Coinglass funding fetch failed")
            coinglass_service.last_error = "funding fetch failed"
            return _funding_cache[1] if _funding_cache else {}
        if payload:
            _funding_cache = (now, payload)
            coinglass_service.last_update = time.time()
            coinglass_service.last_error = None
        return payload


async def get_symbol_derivatives(base: str) -> dict[str, Any]:
    """Funding (from bulk cache) and OI (per symbol, cached) for one base asset."""
    funding_map = await get_funding_map()
    if not funding_map:
        return {}

    resolved = resolve_coinglass_symbol(base, funding_map)
    if resolved is None:
        return {}

    out: dict[str, Any] = {
        "coinglass_symbol": resolved,
        **funding_map[resolved],
    }

    if not coinglass_service.api_key:
        return out

    now = time.monotonic()
    cached = _oi_cache.get(resolved)
    if cached and now - cached[0] < _OI_CACHE_TTL:
        out.update(cached[1])
        return out

    async with _oi_lock:
        now = time.monotonic()
        cached = _oi_cache.get(resolved)
        if cached and now - cached[0] < _OI_CACHE_TTL:
            out.update(cached[1])
        else:
            try:
                async with httpx.AsyncClient(base_url=COINGLASS_REST_URL, headers=_headers(), timeout=30) as client:
                    oi = await fetch_open_interest(client, resolved)
            except Exception:
                logger.debug("Coinglass OI fetch failed for %s", resolved, exc_info=True)
                oi = cached[1] if cached else None
            if oi:
                _oi_cache[resolved] = (now, oi)
                out.update(oi)
            elif cached:
                out.update(cached[1])

    async with _pairs_lock:
        now = time.monotonic()
        cached = _pairs_cache.get(resolved)
        if cached and now - cached[0] < _PAIRS_CACHE_TTL:
            out.update(cached[1])
        else:
            try:
                async with httpx.AsyncClient(base_url=COINGLASS_REST_URL, headers=_headers(), timeout=30) as client:
                    pairs = await fetch_pairs_markets(client, resolved)
            except Exception:
                logger.debug("Coinglass pairs-markets fetch failed for %s", resolved, exc_info=True)
                pairs = cached[1] if cached else None
            if pairs:
                _pairs_cache[resolved] = (now, pairs)
                out.update(pairs)
            elif cached:
                out.update(cached[1])

    async with _funding_hist_lock:
        now = time.monotonic()
        cached = _funding_hist_cache.get(resolved)
        if cached and now - cached[0] < _FUNDING_HIST_CACHE_TTL:
            out.update(cached[1])
        else:
            try:
                async with httpx.AsyncClient(base_url=COINGLASS_REST_URL, headers=_headers(), timeout=30) as client:
                    history = await fetch_funding_history(client, resolved)
            except Exception:
                logger.debug("Coinglass funding history fetch failed for %s", resolved, exc_info=True)
                history = cached[1] if cached else None
            if history:
                _funding_hist_cache[resolved] = (now, history)
                out.update(history)
            elif cached:
                out.update(cached[1])
    return out
