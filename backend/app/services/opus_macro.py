"""External and derived daily series feeding the Opus analysis engine.

Opus needs macro data with *history*, not just a current reading: its weights
are learned from how features behaved on past days, so a live-only snapshot is
useless for calibration. This module fetches long-history series that are free
and key-less, and normalizes everything into ``{series_id: {day_iso: value}}``
for :mod:`opus_store` to persist in ``opus_macro_series``.

Sources:

- **FRED** (`fredgraph.csv`) — US 2y/10y treasury yields and the CBOE VIX close,
  daily back to the 1960s/1990s, no API key. This also covers the documented gap
  where Twelve Data's Pro plan returns 404 for ``US10Y`` and ``VIX``.
- **Alternative.me** — the complete Crypto Fear & Greed history (``limit=0``),
  back to 2018.
- **DeFiLlama** — aggregate USD-pegged stablecoin supply, full history.
- **Coinglass** — per-coin average perpetual funding. Only a current snapshot is
  available on the Hobbyist plan, so it is appended one day at a time and the
  funding features become usable as that history accumulates.

Every fetch is best-effort: a failing source yields no series and Opus simply
runs without it. Nothing here touches the database or the request path.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

import httpx

from .coinglass import coinglass_service, get_funding_map, resolve_coinglass_symbol

logger = logging.getLogger("berebank.opus")

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FEAR_GREED_URL = "https://api.alternative.me/fng/"
STABLECOIN_URL = "https://stablecoins.llama.fi/stablecoincharts/all"

# FRED series id -> Opus series id.
FRED_SERIES = {
    "DGS2": "fred:us2y",
    "DGS10": "fred:us10y",
    "VIXCLS": "fred:vix",
}

SERIES_FEAR_GREED = "crypto:fear_greed"
SERIES_STABLECOIN = "crypto:stablecoin_usd"
FUNDING_PREFIX = "funding:"

_TIMEOUT = 30.0


def _day_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def parse_fred_csv(text: str) -> dict[str, float]:
    """Parse a fredgraph CSV body into ``{day_iso: value}``.

    FRED writes the date in the first column (historically ``DATE``, now
    ``observation_date``) and uses ``.`` for missing observations.
    """
    points: dict[str, float] = {}
    reader = csv.reader(io.StringIO(text))
    for index, row in enumerate(reader):
        if len(row) < 2:
            continue
        if index == 0:
            continue  # header
        day_raw, value_raw = row[0].strip(), row[1].strip()
        if not day_raw or value_raw in ("", "."):
            continue
        try:
            day = datetime.fromisoformat(day_raw).date().isoformat()
            value = float(value_raw)
        except ValueError:
            continue
        points[day] = value
    return points


async def fetch_fred_series(client: httpx.AsyncClient, fred_id: str) -> dict[str, float]:
    resp = await client.get(FRED_CSV_URL, params={"id": fred_id})
    resp.raise_for_status()
    return parse_fred_csv(resp.text)


def parse_fear_greed(payload: dict) -> dict[str, float]:
    points: dict[str, float] = {}
    for item in payload.get("data") or []:
        try:
            day = _day_iso(int(item["timestamp"]) * 1000)
            points[day] = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
    return points


async def fetch_fear_greed(client: httpx.AsyncClient) -> dict[str, float]:
    resp = await client.get(FEAR_GREED_URL, params={"limit": 0, "format": "json"})
    resp.raise_for_status()
    return parse_fear_greed(resp.json())


def parse_stablecoin_supply(payload: list) -> dict[str, float]:
    points: dict[str, float] = {}
    for item in payload or []:
        try:
            day = _day_iso(int(item["date"]) * 1000)
            value = float((item.get("totalCirculating") or {})["peggedUSD"])
        except (KeyError, TypeError, ValueError):
            continue
        points[day] = value
    return points


async def fetch_stablecoin_supply(client: httpx.AsyncClient) -> dict[str, float]:
    resp = await client.get(STABLECOIN_URL)
    resp.raise_for_status()
    return parse_stablecoin_supply(resp.json())


async def fetch_funding_snapshot(crypto_bases: list[str]) -> dict[str, dict[str, float]]:
    """Today's average funding rate per crypto base, as one series per coin.

    Uses the single bulk Coinglass call (already cached by ``coinglass.py``), so
    this adds no per-coin request load.
    """
    if not coinglass_service.api_key:
        return {}
    funding_map = await get_funding_map()
    if not funding_map:
        return {}
    day = _today_iso()
    series: dict[str, dict[str, float]] = {}
    for base in crypto_bases:
        symbol = resolve_coinglass_symbol(base, funding_map)
        if symbol is None:
            continue
        rate = (funding_map.get(symbol) or {}).get("funding_rate_avg")
        if rate is None:
            continue
        try:
            series[f"{FUNDING_PREFIX}{base}"] = {day: float(rate)}
        except (TypeError, ValueError):
            continue
    return series


async def fetch_external_series(crypto_bases: list[str]) -> dict[str, dict[str, float]]:
    """Fetch every external series Opus uses. Missing sources are skipped."""
    series: dict[str, dict[str, float]] = {}
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        for fred_id, series_id in FRED_SERIES.items():
            try:
                points = await fetch_fred_series(client, fred_id)
            except Exception as exc:
                logger.warning("Opus: FRED %s fetch failed: %s", fred_id, exc)
                continue
            if points:
                series[series_id] = points
        try:
            points = await fetch_fear_greed(client)
            if points:
                series[SERIES_FEAR_GREED] = points
        except Exception as exc:
            logger.warning("Opus: Fear & Greed fetch failed: %s", exc)
        try:
            points = await fetch_stablecoin_supply(client)
            if points:
                series[SERIES_STABLECOIN] = points
        except Exception as exc:
            logger.warning("Opus: stablecoin supply fetch failed: %s", exc)
    try:
        series.update(await fetch_funding_snapshot(crypto_bases))
    except Exception as exc:
        logger.warning("Opus: Coinglass funding snapshot failed: %s", exc)
    return series
