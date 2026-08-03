"""Persistence and orchestration for the Opus engine.

Everything that touches the database lives here, so the feature, calibration
and scoring modules stay pure and testable. Responsibilities:

- store and read the harvested macro series (``opus_macro_series``);
- build the daily panel from the shared ``market_candles`` table;
- run the nightly calibration and persist it (``opus_calibration``);
- compute the ranking rows every market is scored with, cached for the request
  path;
- snapshot each day's recommendations and later score them against what the
  market actually did (``opus_recommendations``), which is the engine's live
  track record.

The Opus harvest runs as its own background service and never modifies the
existing candle harvest or any other engine's data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import MarketCandle, OpusCalibration, OpusMacroSeries, OpusRecommendation
from . import opus_analysis, opus_calibration, opus_features, opus_macro
from .market_data import market_data_service

logger = logging.getLogger("berebank.opus")

# Panel depth for scoring: the 70-bar warm-up plus the 63-day relative windows
# need ~135 trading bars, so 260 calendar days covers even holiday-heavy
# exchange calendars without loading years of history into memory.
SCORING_PANEL_DAYS = 260

# Calibration wants as much history as it can get, bounded for predictable
# runtime and memory on small deployments.
CALIBRATION_PANEL_DAYS = 1500

SNAPSHOT_RETENTION_DAYS = 400

HARVEST_STARTUP_DELAY = 90        # after the candle harvest has had a chance
HARVEST_INTERVAL = 6 * 3600
RECALIBRATION_MIN_GAP = 20 * 3600
MIN_LIVE_SAMPLES = 20


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _current_utc_day(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    return _as_utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _day_start(day_iso: str) -> datetime:
    return datetime.fromisoformat(day_iso).replace(tzinfo=timezone.utc)


# ---- macro series ----

def upsert_series(db: Session, series_id: str, points: dict[str, float]) -> int:
    """Insert or update daily values of one series; returns rows written."""
    if not points:
        return 0
    days = {day: _day_start(day) for day in points}
    existing = {
        _as_utc(row.day).date().isoformat(): row
        for row in db.scalars(
            select(OpusMacroSeries).where(
                OpusMacroSeries.series_id == series_id,
                OpusMacroSeries.day >= min(days.values()),
                OpusMacroSeries.day <= max(days.values()),
            )
        )
    }
    written = 0
    for day_iso, value in points.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        row = existing.get(day_iso)
        if row is None:
            db.add(OpusMacroSeries(series_id=series_id, day=days[day_iso], value=numeric))
            written += 1
        elif row.value != numeric:
            row.value = numeric
            written += 1
    db.commit()
    return written


def upsert_many_series(db: Session, series: dict[str, dict[str, float]]) -> int:
    return sum(upsert_series(db, series_id, points) for series_id, points in series.items())


def load_series(db: Session, series_id: str) -> dict[str, float]:
    rows = db.execute(
        select(OpusMacroSeries.day, OpusMacroSeries.value)
        .where(OpusMacroSeries.series_id == series_id)
        .order_by(OpusMacroSeries.day)
    ).all()
    return {_as_utc(day).date().isoformat(): value for day, value in rows}


def load_series_prefix(db: Session, prefix: str) -> dict[str, dict[str, float]]:
    rows = db.execute(
        select(OpusMacroSeries.series_id, OpusMacroSeries.day, OpusMacroSeries.value)
        .where(OpusMacroSeries.series_id.like(f"{prefix}%"))
        .order_by(OpusMacroSeries.series_id, OpusMacroSeries.day)
    ).all()
    out: dict[str, dict[str, float]] = {}
    for series_id, day, value in rows:
        out.setdefault(series_id, {})[_as_utc(day).date().isoformat()] = value
    return out


def series_status(db: Session) -> list[dict]:
    rows = db.execute(
        select(
            OpusMacroSeries.series_id,
            func.count(OpusMacroSeries.id),
            func.min(OpusMacroSeries.day),
            func.max(OpusMacroSeries.day),
        ).group_by(OpusMacroSeries.series_id)
    ).all()
    out = []
    for series_id, count, first, last in rows:
        out.append({
            "series_id": series_id,
            "points": int(count or 0),
            "first_day": _as_utc(first).date().isoformat() if first else None,
            "last_day": _as_utc(last).date().isoformat() if last else None,
        })
    out.sort(key=lambda item: item["series_id"])
    return out


# ---- calibration ----

def save_calibrations(db: Session, calibrations: dict[tuple[str, str, str], dict]) -> int:
    written = 0
    for (group, horizon, regime), payload in calibrations.items():
        row = db.scalar(
            select(OpusCalibration).where(
                OpusCalibration.engine_version == opus_calibration.ENGINE_VERSION,
                OpusCalibration.peer_group == group,
                OpusCalibration.horizon == horizon,
                OpusCalibration.regime == regime,
            )
        )
        encoded = json.dumps(payload)
        samples = int(payload.get("days") or 0)
        if row is None:
            db.add(OpusCalibration(
                engine_version=opus_calibration.ENGINE_VERSION,
                peer_group=group,
                horizon=horizon,
                regime=regime,
                payload=encoded,
                samples=samples,
                calibrated_at=datetime.now(timezone.utc),
            ))
        else:
            row.payload = encoded
            row.samples = samples
            row.calibrated_at = datetime.now(timezone.utc)
        written += 1
    db.commit()
    return written


def load_calibrations(db: Session) -> dict[tuple[str, str, str], dict]:
    rows = db.scalars(
        select(OpusCalibration).where(
            OpusCalibration.engine_version == opus_calibration.ENGINE_VERSION
        )
    ).all()
    out: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        try:
            payload = json.loads(row.payload)
        except (TypeError, ValueError):
            continue
        payload.setdefault("calibrated_at", _as_utc(row.calibrated_at).isoformat())
        out[(row.peer_group, row.horizon, row.regime)] = payload
    return out


def calibration_status(db: Session) -> dict:
    rows = db.execute(
        select(
            func.count(OpusCalibration.id),
            func.max(OpusCalibration.calibrated_at),
        ).where(OpusCalibration.engine_version == opus_calibration.ENGINE_VERSION)
    ).one()
    count, last = rows
    return {
        "engine_version": opus_calibration.ENGINE_VERSION,
        "rows": int(count or 0),
        "calibrated_at": _as_utc(last).isoformat().replace("+00:00", "Z") if last else None,
    }


# ---- panel ----

def load_panel_candles(
    db: Session,
    *,
    since_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, list[list]]:
    """Completed daily candles for every market, oldest first per market.

    The currently forming UTC day is excluded so every feature is computed on
    closed bars — the same basis the calibration measured them on.
    """
    statement = select(
        MarketCandle.market,
        MarketCandle.day,
        MarketCandle.open,
        MarketCandle.high,
        MarketCandle.low,
        MarketCandle.close,
        MarketCandle.volume,
    ).where(MarketCandle.day < _current_utc_day(now))
    if since_days:
        cutoff = _current_utc_day(now) - timedelta(days=since_days)
        statement = statement.where(MarketCandle.day >= cutoff)
    statement = statement.order_by(MarketCandle.market, MarketCandle.day)

    out: dict[str, list[list]] = {}
    for market, day, open_, high, low, close, volume in db.execute(statement):
        timestamp = int(_as_utc(day).timestamp()) * 1000
        out.setdefault(market, []).append([timestamp, open_, high, low, close, volume])
    return out


def _asset_classes() -> dict[str, str]:
    return {
        market: info["asset_class"]
        for market, info in market_data_service.markets.items()
    }


def _funding_by_market(db: Session) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for series_id, points in load_series_prefix(db, opus_macro.FUNDING_PREFIX).items():
        base = series_id[len(opus_macro.FUNDING_PREFIX):]
        if base:
            out[f"{base}-EUR"] = points
    return out


def _changes(points: dict[str, float], *, logarithmic: bool) -> dict[str, float]:
    """Day-over-day change of a level series, aligned to the later day."""
    days = sorted(points)
    out: dict[str, float] = {}
    for previous, day in zip(days, days[1:]):
        before, after = points[previous], points[day]
        if logarithmic:
            if before > 0 and after > 0:
                out[day] = math.log(after / before)
        else:
            out[day] = after - before
    return out


def macro_change_series(db: Session) -> dict[str, dict[str, float]]:
    """Daily changes of the harvested macro levels, keyed for the beta features.

    Levels are useless in a cross-section (they are the same number for every
    market); changes are what a market's returns can be regressed against.
    """
    return {
        "vix": _changes(load_series(db, "fred:vix"), logarithmic=True),
        "rate": _changes(load_series(db, "fred:us10y"), logarithmic=False),
        "fng": _changes(load_series(db, opus_macro.SERIES_FEAR_GREED), logarithmic=False),
        "stable": _changes(load_series(db, opus_macro.SERIES_STABLECOIN), logarithmic=True),
    }


def macro_context(db: Session) -> dict:
    """Current macro backdrop, for display next to the rankings."""
    def _latest(series_id: str) -> tuple[str | None, float | None]:
        points = load_series(db, series_id)
        if not points:
            return None, None
        day = max(points)
        return day, points[day]

    vix_day, vix = _latest("fred:vix")
    _, us10y = _latest("fred:us10y")
    _, us2y = _latest("fred:us2y")
    fng_day, fng = _latest(opus_macro.SERIES_FEAR_GREED)
    stable = load_series(db, opus_macro.SERIES_STABLECOIN)
    stable_change = None
    if len(stable) > 30:
        days = sorted(stable)
        recent, past = stable[days[-1]], stable[days[-31]]
        if past > 0:
            stable_change = (recent / past - 1.0) * 100
    return {
        "vix": None if vix is None else round(vix, 2),
        "vix_day": vix_day,
        "us10y": None if us10y is None else round(us10y, 2),
        "us2y": None if us2y is None else round(us2y, 2),
        "yield_curve": None if us10y is None or us2y is None else round(us10y - us2y, 2),
        "fear_greed": None if fng is None else round(fng),
        "fear_greed_day": fng_day,
        "stablecoin_change_30d_pct": None if stable_change is None else round(stable_change, 2),
    }


def build_panel(
    db: Session,
    *,
    since_days: int | None = SCORING_PANEL_DAYS,
) -> tuple[dict[str, opus_features.MarketSeries], dict[str, dict[str, float]]]:
    """Feature panel plus peer-group index returns, ready for scoring."""
    candles = load_panel_candles(db, since_days=since_days)
    panel = opus_features.build_panel(candles, _asset_classes())
    index_returns = opus_features.group_index_returns(panel)
    opus_features.add_relative_features(panel, index_returns, macro_change_series(db))
    opus_features.attach_series_feature(panel, "funding", _funding_by_market(db))
    return panel, index_returns


def recalibrate(db: Session) -> dict:
    """Rebuild every calibration row from the full stored panel."""
    started = time.monotonic()
    panel, index_returns = build_panel(db, since_days=CALIBRATION_PANEL_DAYS)
    if not panel:
        return {"markets": 0, "rows": 0, "seconds": 0.0}
    calibrations = opus_calibration.calibrate(panel, index_returns)
    rows = save_calibrations(db, calibrations)
    elapsed = time.monotonic() - started
    logger.info(
        "Opus calibration: %d markets, %d rows in %.1fs", len(panel), rows, elapsed
    )
    return {"markets": len(panel), "rows": rows, "seconds": round(elapsed, 1)}


# ---- scoring ----

def _turnover_eur(raw_values: dict[str, float]) -> float | None:
    value = raw_values.get("turnover")
    if value is None:
        return None
    try:
        return math.exp(value)
    except OverflowError:
        return None


def compute_scores(db: Session, *, now: datetime | None = None) -> dict:
    """Score every market on every horizon from the latest completed bars.

    One shared pass over the panel produces the rows for all three horizons;
    only the calibrated weights differ between them. Fee-dependent numbers are
    deliberately left out, so the result can be cached and finalized per user.
    """
    started = time.monotonic()
    panel, index_returns = build_panel(db)
    calibrations = load_calibrations(db)
    reference_day = _current_utc_day(now)
    markets_info = market_data_service.markets

    regimes = {
        group: opus_calibration.current_regime(index_returns.get(group) or {})
        for group in opus_features.PEER_GROUPS
    }

    latest_index: dict[str, int] = {}
    members_by_group: dict[str, list[tuple[str, int]]] = {
        group: [] for group in opus_features.PEER_GROUPS
    }
    for market, series in panel.items():
        index = len(series.days) - 1
        if index < opus_features.WARMUP_BARS:
            continue
        latest_index[market] = index
        members_by_group[series.group].append((market, index))

    horizons = list(opus_calibration.HORIZONS)
    rows_by_horizon: dict[str, list[dict]] = {horizon: [] for horizon in horizons}
    group_days: dict[str, str] = {}
    # Per-market feature detail, kept so the detail endpoint can reuse this
    # cross-section instead of rebuilding the whole panel per request.
    detail: dict[str, dict] = {}

    for group, members in members_by_group.items():
        if not members:
            continue
        z_by_market = opus_features.cross_section(panel, members)
        group_days[group] = max(panel[market].days[index] for market, index in members)
        regime = regimes.get(group, "all")
        for market, index in members:
            series = panel[market]
            raw_values = series.feature_at(index)
            z_scores = z_by_market.get(market) or {}
            info = markets_info.get(market) or {}
            day_iso = series.days[index]
            days_since_close = max(0, (reference_day - _day_start(day_iso)).days - 1)
            close = series.closes[index]
            turnover = _turnover_eur(raw_values)
            detail[market] = {
                "market": market,
                "asset_class": series.asset_class,
                "peer_group": group,
                "peers": len(members),
                "regime": regime,
                "day": day_iso,
                "days_since_close": days_since_close,
                "z_scores": z_scores,
                "raw_values": raw_values,
                "turnover_eur": turnover,
                "expected_move_pct": {
                    horizon: opus_analysis.expected_move_pct(
                        series, index, opus_calibration.HORIZONS[horizon]
                    )
                    for horizon in horizons
                },
            }

            for horizon in horizons:
                bars = opus_calibration.HORIZONS[horizon]
                payload = opus_calibration.pick_payload(
                    calibrations, group, horizon, regime
                )
                scored = opus_analysis.score_market(
                    z_scores, payload, regime=regime, raw_values=raw_values
                )
                outlook = scored["outlook"]
                rows_by_horizon[horizon].append({
                    "market": market,
                    "name": info.get("name"),
                    "asset_class": series.asset_class,
                    "peer_group": group,
                    "regime": regime,
                    "horizon": horizon,
                    "day": day_iso,
                    "days_since_close": days_since_close,
                    "close": f"{close:.10g}",
                    "direction": outlook["direction"],
                    "score": outlook["score"],
                    "confidence": outlook["confidence"],
                    "weights_learned": scored["weights_learned"],
                    "alpha_pct": scored["alpha_pct"],
                    "market_return_pct": scored["market_return_pct"],
                    "expected_return_pct": scored["expected_return_pct"],
                    "expected_move_pct": opus_analysis.expected_move_pct(series, index, bars),
                    "turnover_eur": turnover,
                    "corr_mkt": raw_values.get("corr_mkt"),
                })

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    elapsed = round(time.monotonic() - started, 2)
    return {
        "generated_at": generated_at,
        "engine_version": opus_calibration.ENGINE_VERSION,
        "regimes": regimes,
        "group_days": group_days,
        "markets": len(latest_index),
        "seconds": elapsed,
        "calibrated": bool(calibrations),
        "calibrations": calibrations,
        "macro": macro_context(db),
        "rows": rows_by_horizon,
        "detail": detail,
    }


def detail_context(scores: dict, market: str, horizon: str) -> dict | None:
    """Cross-sectional context for one market, from a cached scores payload.

    Lets the detail endpoint show each feature's percentile, weight and
    information coefficient without rebuilding the panel per request.
    """
    entry = (scores.get("detail") or {}).get(market)
    if entry is None:
        return None
    group = entry["peer_group"]
    payload = opus_calibration.pick_payload(
        scores.get("calibrations") or {}, group, horizon, entry["regime"]
    )
    return {
        **entry,
        "horizon": horizon,
        "calibration": payload,
        "expected_vol_pct": (entry.get("expected_move_pct") or {}).get(horizon),
    }


# ---- recommendation snapshots and live track record ----

def save_snapshot(db: Session, horizon: str, rows: list[dict], day_iso: str | None = None) -> int:
    """Persist today's recommendations so they can be graded later."""
    written = 0
    days = {_day_start(day_iso or row["day"]) for row in rows}
    if not days:
        return 0
    known = {
        (_as_utc(row.day), row.market): row
        for row in db.scalars(
            select(OpusRecommendation).where(
                OpusRecommendation.horizon == horizon,
                OpusRecommendation.day.in_(days),
            )
        )
    }
    for row in rows:
        day = _day_start(day_iso or row["day"])
        existing = known.get((day, row["market"]))
        try:
            close = Decimal(str(row.get("close"))) if row.get("close") else None
        except (InvalidOperation, TypeError):
            close = None
        values = {
            "action": row.get("action") or "hold",
            "direction": row.get("direction") or "none",
            "score": float(row.get("score") or 0),
            "buy_score": float(row.get("buy_score") or 0),
            "sell_score": float(row.get("sell_score") or 0),
            "expected_return_pct": _float_or_none(row.get("expected_return_pct")),
            "net_edge_pct": _float_or_none(row.get("net_edge_pct")),
            "conviction": _float_or_none(row.get("conviction")),
            "buy_rank": int(row["buy_rank"]) if row.get("buy_rank") else None,
            "close_price": close,
        }
        if existing is None:
            db.add(OpusRecommendation(
                day=day, market=row["market"], horizon=horizon, **values
            ))
            written += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)
    db.commit()
    return written


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def evaluate_snapshots(db: Session, *, now: datetime | None = None) -> int:
    """Fill in the realized return of recommendations whose horizon has passed."""
    pending = db.scalars(
        select(OpusRecommendation).where(OpusRecommendation.realized_return_pct.is_(None))
    ).all()
    if not pending:
        return 0
    markets = {row.market for row in pending}
    closes_by_market: dict[str, tuple[list[str], list[float]]] = {}
    statement = (
        select(MarketCandle.market, MarketCandle.day, MarketCandle.close)
        .where(
            MarketCandle.market.in_(markets),
            MarketCandle.day < _current_utc_day(now),
        )
        .order_by(MarketCandle.market, MarketCandle.day)
    )
    for market, day, close in db.execute(statement):
        days, values = closes_by_market.setdefault(market, ([], []))
        days.append(_as_utc(day).date().isoformat())
        values.append(float(close))

    evaluated = 0
    for row in pending:
        entry = closes_by_market.get(row.market)
        if entry is None:
            continue
        days, values = entry
        day_iso = _as_utc(row.day).date().isoformat()
        try:
            start = days.index(day_iso)
        except ValueError:
            continue
        bars = opus_calibration.HORIZONS.get(row.horizon)
        if not bars:
            continue
        target = start + bars
        if target >= len(values):
            continue
        base = values[start]
        if base <= 0:
            continue
        row.realized_return_pct = (values[target] / base - 1.0) * 100
        row.evaluated_at = datetime.now(timezone.utc)
        evaluated += 1
    if evaluated:
        db.commit()
    return evaluated


def live_track_record(
    db: Session,
    horizon: str,
    *,
    market: str | None = None,
) -> dict | None:
    """How the engine's own published recommendations actually performed.

    Counts a directional call (buy/strong_buy or reduce/sell) as a hit when the
    realized move over the horizon went the right way. Returns ``None`` until
    enough graded samples exist.
    """
    statement = select(
        OpusRecommendation.action,
        OpusRecommendation.realized_return_pct,
        OpusRecommendation.day,
    ).where(
        OpusRecommendation.horizon == horizon,
        OpusRecommendation.realized_return_pct.isnot(None),
        OpusRecommendation.action != "hold",
    )
    if market:
        statement = statement.where(OpusRecommendation.market == market)
    rows = db.execute(statement).all()
    if len(rows) < MIN_LIVE_SAMPLES:
        return None

    hits = 0
    buys: list[float] = []
    sells: list[float] = []
    days: list[datetime] = []
    for action, realized, day in rows:
        days.append(_as_utc(day))
        bullish = action in ("buy", "strong_buy")
        if bullish:
            buys.append(realized)
        else:
            sells.append(realized)
        if (realized > 0) == bullish:
            hits += 1
    total = len(rows)

    def _mean(values: list[float]) -> str | None:
        if not values:
            return None
        return f"{sum(values) / len(values):.2f}"

    return {
        "hit_rate_pct": f"{hits / total * 100:.1f}",
        "samples": total,
        "horizon": horizon,
        "buy_samples": len(buys),
        "sell_samples": len(sells),
        "avg_buy_return_pct": _mean(buys),
        "avg_sell_return_pct": _mean(sells),
        "from": min(days).date().isoformat(),
        "to": max(days).date().isoformat(),
    }


def snapshot_status(db: Session) -> dict:
    count, first, last, evaluated = db.execute(
        select(
            func.count(OpusRecommendation.id),
            func.min(OpusRecommendation.day),
            func.max(OpusRecommendation.day),
            func.count(OpusRecommendation.realized_return_pct),
        )
    ).one()
    return {
        "rows": int(count or 0),
        "evaluated": int(evaluated or 0),
        "first_day": _as_utc(first).date().isoformat() if first else None,
        "last_day": _as_utc(last).date().isoformat() if last else None,
    }


def prune_snapshots(db: Session, *, now: datetime | None = None) -> int:
    """Drop snapshots older than the retention window; returns rows removed."""
    cutoff = _current_utc_day(now) - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    result = db.execute(
        delete(OpusRecommendation).where(OpusRecommendation.day < cutoff),
        execution_options={"synchronize_session": False},
    )
    db.commit()
    return int(result.rowcount or 0)


# ---- background service ----

class OpusHarvestService:
    """Refreshes macro series, recalibrates and snapshots recommendations."""

    def __init__(self) -> None:
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self.last_calibration: dict | None = None
        self._task: asyncio.Task | None = None
        self._last_calibrated: float = 0.0

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="opus-harvest")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict:
        return {
            "last_run": self.last_run.isoformat().replace("+00:00", "Z") if self.last_run else None,
            "error": self.last_error,
            "calibration": self.last_calibration,
        }

    async def _run(self) -> None:
        await asyncio.sleep(HARVEST_STARTUP_DELAY)
        while True:
            try:
                await self.harvest_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Opus harvest failed: %s", exc)
                self.last_error = str(exc)
            await asyncio.sleep(HARVEST_INTERVAL)

    async def harvest_once(self) -> None:
        crypto_bases = [
            info["base"]
            for info in market_data_service.markets.values()
            if info["asset_class"] == "crypto"
        ]
        series = await opus_macro.fetch_external_series(crypto_bases)
        db = SessionLocal()
        try:
            if series:
                written = upsert_many_series(db, series)
                logger.info("Opus macro harvest wrote %d points", written)
        finally:
            db.close()

        due = time.monotonic() - self._last_calibrated >= RECALIBRATION_MIN_GAP
        if due or not self._last_calibrated:
            self.last_calibration = await asyncio.to_thread(self._recalibrate_in_thread)
            self._last_calibrated = time.monotonic()

        await asyncio.to_thread(self._snapshot_in_thread)
        self.last_run = datetime.now(timezone.utc)
        self.last_error = None

    def _recalibrate_in_thread(self) -> dict:
        db = SessionLocal()
        try:
            return recalibrate(db)
        finally:
            db.close()

    def _snapshot_in_thread(self) -> None:
        db = SessionLocal()
        try:
            scores = compute_scores(db)
            for horizon, rows in scores["rows"].items():
                finalized = opus_analysis.rank_rows([
                    opus_analysis.finalize_row(
                        row, days_since_close=row.get("days_since_close")
                    )
                    for row in rows
                ])
                save_snapshot(db, horizon, finalized)
            evaluate_snapshots(db)
            prune_snapshots(db)
        finally:
            db.close()


opus_harvest_service = OpusHarvestService()
