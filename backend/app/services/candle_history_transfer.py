"""Export and import persisted daily candle history for dev/prod transfer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AppSetting, MarketCandle
from .candle_store import upsert_candles

EXPORT_VERSION = 1
GTP_DEEP_KEY_PREFIX = "gtp56sol_deep:"


def _as_utc(day: datetime) -> datetime:
    if day.tzinfo is None:
        return day.replace(tzinfo=timezone.utc)
    return day.astimezone(timezone.utc)


def _day_iso(day: datetime) -> str:
    return _as_utc(day).date().isoformat()


def history_status(db: Session) -> dict[str, Any]:
    """Summary of stored candle history for the admin UI."""
    first, last, candle_count, market_count = db.execute(
        select(
            func.min(MarketCandle.day),
            func.max(MarketCandle.day),
            func.count(MarketCandle.id),
            func.count(func.distinct(MarketCandle.market)),
        )
    ).one()
    gtp56sol_deep_markets = db.scalar(
        select(func.count()).select_from(AppSetting).where(
            AppSetting.key.like(f"{GTP_DEEP_KEY_PREFIX}%")
        )
    )
    return {
        "market_count": int(market_count or 0),
        "candle_count": int(candle_count or 0),
        "first_day": _day_iso(first) if first else None,
        "last_day": _day_iso(last) if last else None,
        "gtp56sol_deep_markets": int(gtp56sol_deep_markets or 0),
    }


def export_history(
    db: Session,
    *,
    include_gtp56sol_settings: bool = True,
) -> dict[str, Any]:
    """Build a portable JSON document of stored candle history."""
    rows = db.scalars(
        select(MarketCandle).order_by(MarketCandle.market, MarketCandle.day)
    ).all()
    payload: dict[str, Any] = {
        "version": EXPORT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candles": [
            {
                "market": row.market,
                "day": _day_iso(row.day),
                "open": str(row.open),
                "high": str(row.high),
                "low": str(row.low),
                "close": str(row.close),
                "volume": str(row.volume),
            }
            for row in rows
        ],
    }
    if include_gtp56sol_settings:
        settings = db.scalars(
            select(AppSetting)
            .where(AppSetting.key.like(f"{GTP_DEEP_KEY_PREFIX}%"))
            .order_by(AppSetting.key)
        ).all()
        payload["gtp56sol_deep_settings"] = [
            {"key": setting.key, "value": setting.value}
            for setting in settings
        ]
    return payload


def _parse_candle_row(row: dict[str, Any]) -> tuple[str, list] | None:
    try:
        market = str(row["market"]).strip()
        day = datetime.fromisoformat(str(row["day"])).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
        values = [str(row[key]) for key in ("open", "high", "low", "close", "volume")]
    except (KeyError, TypeError, ValueError):
        return None
    if not market:
        return None
    timestamp_ms = int(day.timestamp()) * 1000
    return market, [timestamp_ms, *values]


def import_history(
    db: Session,
    payload: dict[str, Any],
    *,
    include_settings: bool = True,
) -> dict[str, int]:
    """Upsert candle rows and optional GTP56Sol backfill settings from export."""
    if payload.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {payload.get('version')!r}")

    candles = payload.get("candles")
    if not isinstance(candles, list):
        raise ValueError("Export is missing a candles array")

    grouped: dict[str, list[list]] = {}
    skipped_invalid = 0
    for row in candles:
        if not isinstance(row, dict):
            skipped_invalid += 1
            continue
        parsed = _parse_candle_row(row)
        if parsed is None:
            skipped_invalid += 1
            continue
        market, candle = parsed
        grouped.setdefault(market, []).append(candle)

    rows_written = 0
    for market, market_candles in grouped.items():
        market_candles.sort(key=lambda candle: candle[0])
        rows_written += upsert_candles(db, market, market_candles)

    settings_imported = 0
    if include_settings:
        settings = payload.get("gtp56sol_deep_settings", [])
        if settings is None:
            settings = []
        if not isinstance(settings, list):
            raise ValueError("gtp56sol_deep_settings must be an array when present")
        for item in settings:
            if not isinstance(item, dict):
                skipped_invalid += 1
                continue
            key = str(item.get("key", "")).strip()
            if not key.startswith(GTP_DEEP_KEY_PREFIX):
                skipped_invalid += 1
                continue
            value = item.get("value")
            if value is None:
                skipped_invalid += 1
                continue
            encoded = value if isinstance(value, str) else json.dumps(value)
            setting = db.get(AppSetting, key)
            if setting is None:
                db.add(AppSetting(key=key, value=encoded))
            else:
                setting.value = encoded
            settings_imported += 1
        if settings_imported:
            db.commit()

    return {
        "markets_imported": len(grouped),
        "rows_written": rows_written,
        "settings_imported": settings_imported,
        "skipped_invalid": skipped_invalid,
    }
