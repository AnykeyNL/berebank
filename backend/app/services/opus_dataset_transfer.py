"""Streaming export and import of the Opus dataset for dev/prod transfer.

The Opus dataset is much larger than the candle history it builds on: decades of
macro series, a calibration row per peer group, horizon and regime, and a daily
recommendation snapshot per market. Loading all of that into one JSON document
(as the candle transfer does) would mean holding hundreds of megabytes in
memory on both ends.

This module therefore uses **gzip-compressed NDJSON**: one JSON object per line,
streamed out row by row and read back in bounded batches, so memory use stays
flat no matter how big the file gets. The first line is a header record with the
format version and what the file contains.

    {"type": "header", "version": 1, ...}
    {"type": "macro", "s": "fred:vix", "d": "2026-07-31", "v": 15.99}
    {"type": "calibration", ...}
    {"type": "recommendation", ...}
    {"type": "candle", ...}          # only with include_candles

Candles are optional and off by default: with them a fresh production install
can be seeded from a development machine in a single file. Both gzip and plain
NDJSON are accepted on import. The existing ``/admin/candle-history`` endpoints
are untouched.
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import MarketCandle, OpusCalibration, OpusMacroSeries, OpusRecommendation
from .candle_store import upsert_candles

EXPORT_VERSION = 1

# Rows per transaction on import: large enough to be fast, small enough that a
# huge file never grows the session beyond a few megabytes.
IMPORT_BATCH = 5000

# Rows read per query when streaming an export out of the database.
EXPORT_CHUNK = 5000

_RECORD_TYPES = ("macro", "calibration", "recommendation", "candle")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _day_iso(value: datetime) -> str:
    return _as_utc(value).date().isoformat()


def _parse_day(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )


def dataset_status(db: Session) -> dict[str, Any]:
    """Row counts and coverage of the Opus dataset, for the admin UI."""
    macro_rows, macro_series, macro_first, macro_last = db.execute(
        select(
            func.count(OpusMacroSeries.id),
            func.count(func.distinct(OpusMacroSeries.series_id)),
            func.min(OpusMacroSeries.day),
            func.max(OpusMacroSeries.day),
        )
    ).one()
    calibration_rows, calibrated_at = db.execute(
        select(func.count(OpusCalibration.id), func.max(OpusCalibration.calibrated_at))
    ).one()
    recommendation_rows, evaluated, first_day, last_day = db.execute(
        select(
            func.count(OpusRecommendation.id),
            func.count(OpusRecommendation.realized_return_pct),
            func.min(OpusRecommendation.day),
            func.max(OpusRecommendation.day),
        )
    ).one()
    return {
        "macro_rows": int(macro_rows or 0),
        "macro_series": int(macro_series or 0),
        "macro_first_day": _day_iso(macro_first) if macro_first else None,
        "macro_last_day": _day_iso(macro_last) if macro_last else None,
        "calibration_rows": int(calibration_rows or 0),
        "calibrated_at": _as_utc(calibrated_at) if calibrated_at else None,
        "recommendation_rows": int(recommendation_rows or 0),
        "recommendations_evaluated": int(evaluated or 0),
        "recommendation_first_day": _day_iso(first_day) if first_day else None,
        "recommendation_last_day": _day_iso(last_day) if last_day else None,
    }


# ---- export ----

def _iter_records(db: Session, *, include_candles: bool) -> Iterator[dict]:
    """Yield every dataset row as a compact record, oldest first per table."""
    statement = select(
        OpusMacroSeries.series_id, OpusMacroSeries.day, OpusMacroSeries.value
    ).order_by(OpusMacroSeries.series_id, OpusMacroSeries.day)
    for series_id, day, value in db.execute(statement).yield_per(EXPORT_CHUNK):
        yield {"type": "macro", "s": series_id, "d": _day_iso(day), "v": value}

    for row in db.scalars(
        select(OpusCalibration).order_by(OpusCalibration.id)
    ).yield_per(EXPORT_CHUNK):
        yield {
            "type": "calibration",
            "engine_version": row.engine_version,
            "peer_group": row.peer_group,
            "horizon": row.horizon,
            "regime": row.regime,
            "payload": row.payload,
            "samples": row.samples,
            "calibrated_at": _as_utc(row.calibrated_at).isoformat(),
        }

    for row in db.scalars(
        select(OpusRecommendation).order_by(OpusRecommendation.day, OpusRecommendation.market)
    ).yield_per(EXPORT_CHUNK):
        yield {
            "type": "recommendation",
            "day": _day_iso(row.day),
            "market": row.market,
            "horizon": row.horizon,
            "action": row.action,
            "direction": row.direction,
            "score": row.score,
            "buy_score": row.buy_score,
            "sell_score": row.sell_score,
            "expected_return_pct": row.expected_return_pct,
            "net_edge_pct": row.net_edge_pct,
            "conviction": row.conviction,
            "buy_rank": row.buy_rank,
            "close_price": None if row.close_price is None else str(row.close_price),
            "realized_return_pct": row.realized_return_pct,
            "evaluated_at": None if row.evaluated_at is None else _as_utc(row.evaluated_at).isoformat(),
        }

    if include_candles:
        statement = select(
            MarketCandle.market,
            MarketCandle.day,
            MarketCandle.open,
            MarketCandle.high,
            MarketCandle.low,
            MarketCandle.close,
            MarketCandle.volume,
        ).order_by(MarketCandle.market, MarketCandle.day)
        for market, day, open_, high, low, close, volume in db.execute(
            statement
        ).yield_per(EXPORT_CHUNK):
            yield {
                "type": "candle",
                "m": market,
                "d": _day_iso(day),
                "o": str(open_),
                "h": str(high),
                "l": str(low),
                "c": str(close),
                "v": str(volume),
            }


def export_dataset(db: Session, *, include_candles: bool = False) -> Iterator[bytes]:
    """Stream the dataset as gzip NDJSON chunks, at constant memory."""
    header = {
        "type": "header",
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "include_candles": include_candles,
        "status": {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in dataset_status(db).items()
        },
    }
    buffer = bytearray()
    stream = _GzipChunks(buffer)
    stream.write(header)
    for record in _iter_records(db, include_candles=include_candles):
        stream.write(record)
        if len(buffer) >= 256 * 1024:
            yield bytes(buffer)
            buffer.clear()
    stream.close()
    if buffer:
        yield bytes(buffer)


class _GzipChunks:
    """Writes JSON lines into a gzip stream backed by a bytearray."""

    def __init__(self, buffer: bytearray) -> None:
        self._buffer = buffer
        self._sink = _BufferSink(buffer)
        self._gzip = gzip.GzipFile(fileobj=self._sink, mode="wb", compresslevel=6, mtime=0)

    def write(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), default=str)
        self._gzip.write(line.encode("utf-8"))
        self._gzip.write(b"\n")

    def close(self) -> None:
        self._gzip.close()


class _BufferSink:
    """Minimal binary sink so GzipFile can append into a bytearray."""

    def __init__(self, buffer: bytearray) -> None:
        self._buffer = buffer

    def write(self, data: bytes) -> int:
        self._buffer.extend(data)
        return len(data)

    def flush(self) -> None:
        return None


# ---- import ----

def _open_lines(stream: BinaryIO) -> Iterator[bytes]:
    """Iterate lines from a gzip or plain NDJSON stream."""
    head = stream.read(2)
    stream.seek(0)
    if head == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=stream, mode="rb") as unzipped:
            yield from unzipped
    else:
        yield from stream


class _MacroBuffer:
    """Batches macro rows, resolving conflicts against what is already stored."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.pending: dict[tuple[str, str], float] = {}
        self.written = 0

    def add(self, series_id: str, day_iso: str, value: float) -> None:
        self.pending[(series_id, day_iso)] = value
        if len(self.pending) >= IMPORT_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        series_ids = {series_id for series_id, _ in self.pending}
        existing = {
            (row.series_id, _day_iso(row.day)): row
            for row in self.db.scalars(
                select(OpusMacroSeries).where(OpusMacroSeries.series_id.in_(series_ids))
            )
        }
        for (series_id, day_iso), value in self.pending.items():
            row = existing.get((series_id, day_iso))
            if row is None:
                self.db.add(OpusMacroSeries(
                    series_id=series_id, day=_parse_day(day_iso), value=value
                ))
                self.written += 1
            elif row.value != value:
                row.value = value
                self.written += 1
        self.db.commit()
        self.pending.clear()


class _RecommendationBuffer:
    """Batches recommendation snapshots, upserting on (day, market, horizon)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.pending: dict[tuple[str, str, str], dict] = {}
        self.written = 0

    def add(self, record: dict) -> None:
        key = (str(record["day"]), str(record["market"]), str(record["horizon"]))
        self.pending[key] = record
        if len(self.pending) >= IMPORT_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        days = {_parse_day(day) for day, _, _ in self.pending}
        existing = {
            (_day_iso(row.day), row.market, row.horizon): row
            for row in self.db.scalars(
                select(OpusRecommendation).where(OpusRecommendation.day.in_(days))
            )
        }
        for key, record in self.pending.items():
            values = _recommendation_values(record)
            row = existing.get(key)
            if row is None:
                self.db.add(OpusRecommendation(
                    day=_parse_day(record["day"]),
                    market=str(record["market"]),
                    horizon=str(record["horizon"]),
                    **values,
                ))
            else:
                for field, value in values.items():
                    setattr(row, field, value)
            self.written += 1
        self.db.commit()
        self.pending.clear()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recommendation_values(record: dict) -> dict:
    close_price = None
    if record.get("close_price") is not None:
        try:
            close_price = Decimal(str(record["close_price"]))
        except InvalidOperation:
            close_price = None
    evaluated_at = None
    if record.get("evaluated_at"):
        try:
            evaluated_at = datetime.fromisoformat(str(record["evaluated_at"]))
        except ValueError:
            evaluated_at = None
    return {
        "action": str(record.get("action") or "hold"),
        "direction": str(record.get("direction") or "none"),
        "score": _float_or_none(record.get("score")) or 0.0,
        "buy_score": _float_or_none(record.get("buy_score")) or 0.0,
        "sell_score": _float_or_none(record.get("sell_score")) or 0.0,
        "expected_return_pct": _float_or_none(record.get("expected_return_pct")),
        "net_edge_pct": _float_or_none(record.get("net_edge_pct")),
        "conviction": _float_or_none(record.get("conviction")),
        "buy_rank": None if record.get("buy_rank") is None else int(record["buy_rank"]),
        "close_price": close_price,
        "realized_return_pct": _float_or_none(record.get("realized_return_pct")),
        "evaluated_at": evaluated_at,
    }


def _import_calibration(db: Session, record: dict) -> None:
    row = db.scalar(
        select(OpusCalibration).where(
            OpusCalibration.engine_version == record["engine_version"],
            OpusCalibration.peer_group == record["peer_group"],
            OpusCalibration.horizon == record["horizon"],
            OpusCalibration.regime == record["regime"],
        )
    )
    payload = record["payload"]
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    calibrated_at = datetime.now(timezone.utc)
    if record.get("calibrated_at"):
        try:
            calibrated_at = datetime.fromisoformat(str(record["calibrated_at"]))
        except ValueError:
            pass
    if row is None:
        db.add(OpusCalibration(
            engine_version=str(record["engine_version"]),
            peer_group=str(record["peer_group"]),
            horizon=str(record["horizon"]),
            regime=str(record["regime"]),
            payload=payload,
            samples=int(record.get("samples") or 0),
            calibrated_at=calibrated_at,
        ))
    else:
        row.payload = payload
        row.samples = int(record.get("samples") or 0)
        row.calibrated_at = calibrated_at


def import_dataset(db: Session, stream: BinaryIO) -> dict[str, int]:
    """Import a dataset file, committing in batches at constant memory."""
    lines = _open_lines(stream)
    try:
        first = next(lines)
    except StopIteration:
        raise ValueError("Export file is empty") from None
    try:
        header = json.loads(first)
    except json.JSONDecodeError as exc:
        raise ValueError(f"First line is not valid JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("type") != "header":
        raise ValueError("Export file must start with a header record")
    if header.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {header.get('version')!r}")

    macro = _MacroBuffer(db)
    recommendations = _RecommendationBuffer(db)
    counts = {key: 0 for key in _RECORD_TYPES}
    skipped_invalid = 0
    candles: dict[str, list[list]] = {}
    candle_rows = 0
    pending_candles = 0

    def flush_candles() -> int:
        nonlocal candles, pending_candles
        written = 0
        for market, rows in candles.items():
            rows.sort(key=lambda candle: candle[0])
            written += upsert_candles(db, market, rows)
        candles = {}
        pending_candles = 0
        return written

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            skipped_invalid += 1
            continue
        if not isinstance(record, dict):
            skipped_invalid += 1
            continue
        kind = record.get("type")
        try:
            if kind == "macro":
                value = _float_or_none(record["v"])
                if value is None:
                    raise ValueError("missing value")
                macro.add(str(record["s"]), str(record["d"]), value)
            elif kind == "calibration":
                _import_calibration(db, record)
                db.commit()
            elif kind == "recommendation":
                recommendations.add(record)
            elif kind == "candle":
                day = _parse_day(record["d"])
                candles.setdefault(str(record["m"]), []).append([
                    int(day.timestamp()) * 1000,
                    str(record["o"]),
                    str(record["h"]),
                    str(record["l"]),
                    str(record["c"]),
                    str(record["v"]),
                ])
                pending_candles += 1
                if pending_candles >= IMPORT_BATCH:
                    candle_rows += flush_candles()
            else:
                skipped_invalid += 1
                continue
        except (KeyError, TypeError, ValueError):
            skipped_invalid += 1
            continue
        counts[kind] += 1

    macro.flush()
    recommendations.flush()
    candle_rows += flush_candles()

    return {
        "macro_rows": macro.written,
        "macro_records": counts["macro"],
        "calibration_rows": counts["calibration"],
        "recommendation_rows": recommendations.written,
        "candle_rows": candle_rows,
        "skipped_invalid": skipped_invalid,
    }
