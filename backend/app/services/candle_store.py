"""Shared daily candle persistence for KimiK3 and GTP56Sol.

Harvests daily OHLCV candles for every market into the ``market_candles``
table: crypto from Bitvavo, stocks/funds/commodities from Twelve Data.
Runs a catch-up shortly after startup and then every six hours. Twelve
Data calls are throttled and limited to once per day to stay within the
API credit budget (the still-forming current day is refreshed on every
run, so crypto stays fresh between Twelve Data harvests). Kimi reads a
bounded latest-400 view; GTP56Sol may lazily retain deeper primary history.
"""
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import BITVAVO_REST_URL
from ..database import SessionLocal
from ..models import AppSetting, MarketCandle
from .market_data import market_data_service
from .twelvedata import twelvedata_service

logger = logging.getLogger("berebank.candles")

HISTORY_BARS = 400               # daily bars fetched per market (~1.5 years)
# Retain lazy GTP56Sol backfills for roughly 5.5 years. Normal Kimi harvesting
# still requests only HISTORY_BARS, so global provider traffic is unchanged.
# Storage grows only for markets individually requested through GTP56Sol.
RETENTION_DAYS = 2000
HARVEST_INTERVAL = 6 * 3600      # seconds between harvests
TWELVEDATA_MIN_GAP = 20 * 3600   # Twelve Data harvest at most once per day
STARTUP_DELAY = 30               # let the market catalog load first
BITVAVO_DELAY = 0.1              # ~430 crypto markets, well under rate limits
TWELVEDATA_DELAY = 8.0           # ~8 requests/minute free-tier limit
GTP_DEEP_HISTORY_BARS = 1825     # target five years of daily bars
GTP_DEEP_FAILURE_COOLDOWN = 3600
GTP_DEEP_SUCCESS_COOLDOWN = 24 * 3600
GTP_DEEP_COMPLETE_COOLDOWN = 30 * 24 * 3600
GTP_BITVAVO_PAGE_SIZE = 1000
GTP_BITVAVO_MAX_PAGES = 2
GTP_TWELVEDATA_MIN_GAP = 7.5

_gtp_deep_history_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_gtp_td_spacing_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_gtp_td_last_request = 0.0
_monotonic = time.monotonic
_sleep = asyncio.sleep


@dataclass(frozen=True)
class CandleHistorySummary:
    first_ts: int | None
    last_ts: int | None
    count: int


def _as_utc(day: datetime) -> datetime:
    if day.tzinfo is None:
        return day.replace(tzinfo=timezone.utc)
    return day.astimezone(timezone.utc)


def _current_utc_day(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    return _as_utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _validate_provider_candles(candles: list[list]) -> list[list]:
    """Return finite, valid, deterministically ordered provider daily rows."""
    valid: dict[int, list] = {}
    for candle in candles:
        try:
            if len(candle) < 6:
                continue
            timestamp = int(candle[0])
            values = [float(candle[index]) for index in range(1, 6)]
        except (TypeError, ValueError, OverflowError):
            continue
        open_, high, low, close, volume = values
        if (
            timestamp < 0
            or not all(math.isfinite(value) for value in values)
            or min(open_, high, low, close) <= 0
            or volume < 0
            or high < low
        ):
            continue
        valid[timestamp] = list(candle[:6])
    return [valid[timestamp] for timestamp in sorted(valid)]


def upsert_candles(db: Session, market: str, candles: list[list]) -> int:
    """Insert or refresh daily rows for ``market``; returns rows written.

    ``candles`` are API-shape [timestamp_ms, o, h, l, c, v] oldest first.
    Existing rows are only rewritten when values changed (the current,
    still-forming day drifts between harvests).
    """
    candles = _validate_provider_candles(candles)
    if not candles:
        return 0
    incoming_days = [
        datetime.fromtimestamp(int(candle[0]) / 1000, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for candle in candles
    ]
    # SQLite returns naive datetimes and PostgreSQL returns aware datetimes.
    # Normalize both explicitly to UTC before matching calendar days.
    existing = {
        _as_utc(row.day).date(): row
        for row in db.scalars(
            select(MarketCandle).where(
                MarketCandle.market == market,
                MarketCandle.day >= min(incoming_days),
                MarketCandle.day <= max(incoming_days),
            )
        )
    }
    written = 0
    for candle, day in zip(candles, incoming_days):
        _, o, h, l, c, v = candle
        values = [Decimal(str(x)) for x in (o, h, l, c, v)]
        row = existing.get(day.date())
        if row is None:
            db.add(MarketCandle(
                market=market, day=day,
                open=values[0], high=values[1], low=values[2], close=values[3], volume=values[4],
            ))
            written += 1
        elif [row.open, row.high, row.low, row.close, row.volume] != values:
            row.open, row.high, row.low, row.close, row.volume = values
            written += 1
    db.commit()
    return written


def prune_candles(db: Session, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db.execute(
        delete(MarketCandle).where(
            MarketCandle.day < now - timedelta(days=RETENTION_DAYS)
        ),
        execution_options={"synchronize_session": False},
    )
    db.commit()


def load_daily_candles(db: Session, market: str) -> list[list]:
    """Stored daily candles in API shape [timestamp_ms, o, h, l, c, v], oldest first."""
    rows = db.scalars(
        select(MarketCandle).where(MarketCandle.market == market).order_by(MarketCandle.day)
    ).all()
    candles = []
    for row in rows:
        day = row.day
        if day.tzinfo is None:
            # SQLite returns naive datetimes; stored days are UTC midnight.
            day = day.replace(tzinfo=timezone.utc)
        candles.append([
            int(day.timestamp()) * 1000,
            str(row.open), str(row.high), str(row.low), str(row.close), str(row.volume),
        ])
    return candles


def load_recent_daily_candles(
    db: Session,
    market: str,
    *,
    limit: int = HISTORY_BARS,
) -> list[list]:
    """Latest bounded stored daily rows, oldest first, for Kimi semantics."""
    rows = db.scalars(
        select(MarketCandle)
        .where(MarketCandle.market == market)
        .order_by(MarketCandle.day.desc())
        .limit(limit)
    ).all()
    candles = []
    for row in reversed(rows):
        day = _as_utc(row.day)
        candles.append([
            int(day.timestamp()) * 1000,
            str(row.open), str(row.high), str(row.low), str(row.close), str(row.volume),
        ])
    return candles


def load_completed_daily_candles(
    db: Session,
    market: str,
    *,
    now: datetime | None = None,
) -> list[list]:
    """Stored UTC daily candles excluding the currently forming UTC day."""
    current_day = _current_utc_day(now)
    rows = db.scalars(
        select(MarketCandle)
        .where(MarketCandle.market == market, MarketCandle.day < current_day)
        .order_by(MarketCandle.day)
    ).all()
    candles = []
    for row in rows:
        day = _as_utc(row.day)
        candles.append([
            int(day.timestamp()) * 1000,
            str(row.open), str(row.high), str(row.low), str(row.close), str(row.volume),
        ])
    return candles


def completed_history_summary(
    db: Session,
    market: str,
    *,
    now: datetime | None = None,
) -> CandleHistorySummary:
    """Efficient first/last/count identity for completed UTC daily rows."""
    first, last, count = db.execute(
        select(
            func.min(MarketCandle.day),
            func.max(MarketCandle.day),
            func.count(MarketCandle.id),
        ).where(
            MarketCandle.market == market,
            MarketCandle.day < _current_utc_day(now),
        )
    ).one()
    return CandleHistorySummary(
        first_ts=int(_as_utc(first).timestamp()) * 1000 if first else None,
        last_ts=int(_as_utc(last).timestamp()) * 1000 if last else None,
        count=int(count or 0),
    )


def completed_history_summaries(
    db: Session,
    markets: list[str] | tuple[str, ...],
    *,
    now: datetime | None = None,
) -> dict[str, CandleHistorySummary]:
    """Completed first/last/count identities for many markets in one query."""
    if not markets:
        return {}
    rows = db.execute(
        select(
            MarketCandle.market,
            func.min(MarketCandle.day),
            func.max(MarketCandle.day),
            func.count(MarketCandle.id),
        )
        .where(
            MarketCandle.market.in_(markets),
            MarketCandle.day < _current_utc_day(now),
        )
        .group_by(MarketCandle.market)
    ).all()
    return {
        market: CandleHistorySummary(
            first_ts=int(_as_utc(first).timestamp()) * 1000,
            last_ts=int(_as_utc(last).timestamp()) * 1000,
            count=int(count),
        )
        for market, first, last, count in rows
    }


def _loop_lock(
    locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]],
    key: str,
) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    existing = locks.get(key)
    if existing is None or existing[0] is not loop:
        lock = asyncio.Lock()
        locks[key] = (loop, lock)
        return lock
    return existing[1]


async def _wait_for_gtp_twelvedata_slot() -> None:
    """Serialize all candle-history Twelve Data calls to at most eight/minute."""
    global _gtp_td_last_request
    lock = _loop_lock(_gtp_td_spacing_locks, "twelvedata")
    async with lock:
        if _gtp_td_last_request:
            wait = GTP_TWELVEDATA_MIN_GAP - (_monotonic() - _gtp_td_last_request)
            if wait > 0:
                await _sleep(wait)
        _gtp_td_last_request = _monotonic()


async def _fetch_gtp56sol_deep_history(
    market: str,
    asset_class: str,
) -> list[list]:
    """Fetch one bounded deep-history window using the market's existing provider."""
    if asset_class == "crypto":
        candles_by_timestamp: dict[int, list] = {}
        end: int | None = None
        async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=20) as client:
            for _ in range(GTP_BITVAVO_MAX_PAGES):
                params: dict[str, int | str] = {
                    "interval": "1d",
                    "limit": GTP_BITVAVO_PAGE_SIZE,
                }
                if end is not None:
                    params["end"] = end
                response = await client.get(f"/{market}/candles", params=params)
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list) or not page:
                    break
                for candle in page:
                    if isinstance(candle, list) and candle:
                        candles_by_timestamp[int(candle[0])] = candle
                earliest = min(int(candle[0]) for candle in page)
                end = earliest - 1
                if len(page) < GTP_BITVAVO_PAGE_SIZE:
                    break
        return [
            candles_by_timestamp[timestamp]
            for timestamp in sorted(candles_by_timestamp)
        ][-GTP_DEEP_HISTORY_BARS:]

    # Twelve Data accepts up to 5,000 output rows. Reuse its existing
    # conversion/API-key path and make exactly one daily time-series request.
    await _wait_for_gtp_twelvedata_slot()
    return await twelvedata_service.fetch_candles(
        market,
        "365d",
        extra_bars=GTP_DEEP_HISTORY_BARS - 250,
    )


def _save_gtp_deep_state(db: Session, key: str, state: dict) -> None:
    """Persist cooldown state, tolerating a concurrent first insert."""
    encoded = json.dumps(state)
    setting = db.get(AppSetting, key, populate_existing=True)
    if setting is None:
        db.add(AppSetting(key=key, value=encoded))
    else:
        setting.value = encoded
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Another request/process may have inserted the same key first.
        setting = db.get(AppSetting, key, populate_existing=True)
        if setting is None:
            raise
        setting.value = encoded
        db.commit()


async def ensure_gtp56sol_deep_history(
    db: Session,
    market: str,
    asset_class: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Lazily backfill one market with persisted outcome-aware cooldowns."""
    now = _as_utc(now or datetime.now(timezone.utc))
    now_epoch = now.timestamp()
    summary = completed_history_summary(db, market, now=now)
    if summary.count >= GTP_DEEP_HISTORY_BARS:
        return {"status": "complete", "summary": summary}
    lock = _loop_lock(_gtp_deep_history_locks, market)
    async with lock:
        summary = completed_history_summary(db, market, now=now)
        if summary.count >= GTP_DEEP_HISTORY_BARS:
            return {"status": "complete", "summary": summary}
        setting_key = f"gtp56sol_deep:{market}"
        setting = db.get(AppSetting, setting_key, populate_existing=True)
        state = {}
        if setting is not None:
            try:
                state = json.loads(setting.value)
            except (TypeError, ValueError):
                state = {}
        if now_epoch < float(state.get("next_attempt_at", 0)):
            return {"status": "cooldown", "summary": summary}
        try:
            provider_rows = await _fetch_gtp56sol_deep_history(market, asset_class)
            candles = _validate_provider_candles(provider_rows)
            before = summary
            if not candles:
                state = {
                    "status": "failure",
                    "attempted_at": now_epoch,
                    "next_attempt_at": now_epoch + GTP_DEEP_FAILURE_COOLDOWN,
                    "first_ts": summary.first_ts,
                    "last_ts": summary.last_ts,
                    "count": summary.count,
                }
                _save_gtp_deep_state(db, setting_key, state)
                return {"status": "failure", "summary": summary}
            upsert_candles(db, market, candles)
            after = completed_history_summary(db, market, now=now)
            added_older = (
                after.first_ts is not None
                and (before.first_ts is None or after.first_ts < before.first_ts)
            )
            complete = (
                after.count >= GTP_DEEP_HISTORY_BARS
                or not added_older
            )
            status = "complete" if complete else "success"
            cooldown = (
                GTP_DEEP_COMPLETE_COOLDOWN
                if complete
                else GTP_DEEP_SUCCESS_COOLDOWN
            )
            state = {
                "status": status,
                "attempted_at": now_epoch,
                "next_attempt_at": now_epoch + cooldown,
                "first_ts": after.first_ts,
                "last_ts": after.last_ts,
                "count": after.count,
            }
            _save_gtp_deep_state(db, setting_key, state)
            return {"status": status, "summary": after}
        except Exception as exc:
            db.rollback()
            state = {
                "status": "failure",
                "attempted_at": now_epoch,
                "next_attempt_at": now_epoch + GTP_DEEP_FAILURE_COOLDOWN,
                "first_ts": summary.first_ts,
                "last_ts": summary.last_ts,
                "count": summary.count,
            }
            _save_gtp_deep_state(db, setting_key, state)
            logger.warning("GTP56Sol deep-history fetch failed for %s: %s", market, exc)
            return {"status": "failure", "summary": summary}


class CandleHarvestService:
    def __init__(self) -> None:
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._last_td_harvest: float = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="candle-harvest")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(STARTUP_DELAY)
        while True:
            await self._harvest_once()
            await asyncio.sleep(HARVEST_INTERVAL)

    async def _harvest_once(self) -> None:
        markets = market_data_service.markets
        crypto = [m for m, info in markets.items() if info["asset_class"] == "crypto"]
        other = [m for m, info in markets.items() if info["asset_class"] != "crypto"]
        db = SessionLocal()
        written = 0
        try:
            async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
                for market in crypto:
                    try:
                        resp = await client.get(
                            f"/{market}/candles",
                            params={"interval": "1d", "limit": HISTORY_BARS},
                        )
                        if resp.status_code == 200:
                            candles = sorted(resp.json(), key=lambda c: c[0])
                            written += upsert_candles(db, market, candles)
                    except Exception as exc:
                        logger.warning("Candle harvest failed for %s: %s", market, exc)
                    await asyncio.sleep(BITVAVO_DELAY)

            td_due = time.monotonic() - self._last_td_harvest >= TWELVEDATA_MIN_GAP
            if other and twelvedata_service.api_key and td_due:
                for market in other:
                    try:
                        await _wait_for_gtp_twelvedata_slot()
                        candles = await twelvedata_service.fetch_candles(
                            market, "365d", extra_bars=HISTORY_BARS - 250
                        )
                        written += upsert_candles(db, market, candles)
                    except Exception as exc:
                        logger.warning("Candle harvest failed for %s: %s", market, exc)
                    await asyncio.sleep(TWELVEDATA_DELAY)
                self._last_td_harvest = time.monotonic()

            prune_candles(db)
            self.last_run = datetime.now(timezone.utc)
            self.last_error = None
            logger.info("Candle harvest wrote %d rows", written)
        except Exception as exc:
            logger.exception("Candle harvest failed: %s", exc)
            self.last_error = str(exc)
        finally:
            db.close()


candle_harvest_service = CandleHarvestService()
