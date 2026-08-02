"""Standalone verification of candle history export/import.

Run: .venv\\Scripts\\python test_candle_history_transfer.py
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AppSetting, MarketCandle
from app.services.candle_history_transfer import (
    EXPORT_VERSION,
    export_history,
    history_status,
    import_history,
)
from app.services.candle_store import load_daily_candles

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)


def seed(db: Session) -> None:
    day = datetime(2024, 6, 1, tzinfo=timezone.utc)
    db.add(MarketCandle(
        market="BTC-EUR",
        day=day,
        open=Decimal("50000"),
        high=Decimal("51000"),
        low=Decimal("49000"),
        close=Decimal("50500"),
        volume=Decimal("100"),
    ))
    db.add(MarketCandle(
        market="ETH-EUR",
        day=day,
        open=Decimal("3000"),
        high=Decimal("3100"),
        low=Decimal("2900"),
        close=Decimal("3050"),
        volume=Decimal("200"),
    ))
    db.add(AppSetting(
        key="gtp56sol_deep:BTC-EUR",
        value='{"status":"complete","count":1825}',
    ))
    db.commit()


print("history status")
with Session(engine) as db:
    seed(db)
    status = history_status(db)
    check("counts markets", status["market_count"] == 2)
    check("counts candles", status["candle_count"] == 2)
    check("tracks gtp deep settings", status["gtp56sol_deep_markets"] == 1)

print("export")
with Session(engine) as db:
    payload = export_history(db)
    check("export version", payload["version"] == EXPORT_VERSION)
    check("exports candles", len(payload["candles"]) == 2)
    check("exports gtp settings", len(payload["gtp56sol_deep_settings"]) == 1)
    payload_no_settings = export_history(db, include_gtp56sol_settings=False)
    check("can omit settings", "gtp56sol_deep_settings" not in payload_no_settings)

print("import upsert")
empty_engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(empty_engine)
with Session(empty_engine) as db:
    result = import_history(db, payload)
    check("imports markets", result["markets_imported"] == 2)
    check("writes rows", result["rows_written"] == 2)
    check("imports settings", result["settings_imported"] == 1)
    btc = load_daily_candles(db, "BTC-EUR")
    check("btc close preserved", bool(btc) and btc[0][4] in ("50500", "50500.00"))

print("import updates existing")
with Session(engine) as db:
    updated = dict(payload)
    updated["candles"] = [
        {
            "market": "BTC-EUR",
            "day": "2024-06-01",
            "open": "50000",
            "high": "52000",
            "low": "49000",
            "close": "51500",
            "volume": "150",
        }
    ]
    result = import_history(db, updated, include_settings=False)
    check("updates one market", result["markets_imported"] == 1)
    check("rewrites changed row", result["rows_written"] == 1)
    btc = load_daily_candles(db, "BTC-EUR")
    check("btc close updated", btc[0][4] in ("51500", "51500.00"))

print("validation")
with Session(engine) as db:
    try:
        import_history(db, {"version": 99, "candles": []})
        check("rejects bad version", False)
    except ValueError:
        check("rejects bad version", True)
    try:
        import_history(db, {"version": EXPORT_VERSION, "candles": "nope"})
        check("rejects bad candles", False)
    except ValueError:
        check("rejects bad candles", True)
    bad = {
        "version": EXPORT_VERSION,
        "candles": [{"market": "X", "day": "bad", "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
    }
    result = import_history(db, bad, include_settings=False)
    check("skips invalid rows", result["skipped_invalid"] == 1 and result["markets_imported"] == 0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
