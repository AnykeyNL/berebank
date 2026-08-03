"""Standalone verification of the Opus gzip-NDJSON dataset transfer.

Covers the export format, a full dev -> prod round trip (with and without
candles), idempotent re-import, a large synthetic dataset streamed at constant
memory, and the error paths for empty, headerless, mis-versioned, corrupt and
truncated files.

Run: .venv\\Scripts\\python test_opus_dataset_transfer.py
"""
import gzip
import io
import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import MarketCandle, OpusCalibration, OpusMacroSeries, OpusRecommendation
from app.services import opus_dataset_transfer as transfer

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


DAY = datetime(2026, 7, 1, tzinfo=timezone.utc)


def fresh_db() -> Session:
    """An empty in-memory database with every table created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def day_at(offset: int) -> datetime:
    return DAY + timedelta(days=offset)


def seed(db: Session, *, markets: int = 3, days: int = 4, macro_days: int = 5) -> None:
    """A small but complete dataset: macro series, calibration, snapshots, candles."""
    for i in range(macro_days):
        db.add(OpusMacroSeries(series_id="fred:vix", day=day_at(i), value=15.0 + i))
        db.add(OpusMacroSeries(series_id="crypto:fear_greed", day=day_at(i), value=30.0 + i))

    for horizon in ("1d", "1w"):
        db.add(OpusCalibration(
            engine_version="opus-1",
            peer_group="crypto",
            horizon=horizon,
            regime="all",
            payload=json.dumps({
                "weights": {"mom_21": 0.4, "rev_5": -0.2},
                "walk_forward_ic": 0.0524,
                "alpha_bins": [-0.9, None, 0.1],
            }),
            samples=1234,
            calibrated_at=day_at(macro_days),
        ))

    for i in range(days):
        for m in range(markets):
            market = f"M{m}-EUR"
            evaluated = i < days - 1
            db.add(OpusRecommendation(
                day=day_at(i),
                market=market,
                horizon="1w",
                action="buy" if m == 0 else "hold",
                direction="bullish" if m == 0 else "neutral",
                score=10.0 * m,
                buy_score=20.0 * m,
                sell_score=0.0,
                expected_return_pct=0.5 + m,
                net_edge_pct=0.25 + m,
                conviction=0.3,
                buy_rank=m + 1,
                close_price=Decimal("123.45") + m,
                realized_return_pct=1.5 if evaluated else None,
                evaluated_at=day_at(i + 5) if evaluated else None,
            ))
            db.add(MarketCandle(
                market=market,
                day=day_at(i),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("95"),
                close=Decimal("105.5"),
                volume=Decimal("2500"),
            ))
    db.commit()


def dump(db: Session, *, include_candles: bool = False) -> bytes:
    return b"".join(transfer.export_dataset(db, include_candles=include_candles))


def lines(blob: bytes) -> list[dict]:
    text = gzip.decompress(blob).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def counts(records: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for record in records:
        out[record["type"]] = out.get(record["type"], 0) + 1
    return out


def ndjson(records: list[dict], *, gzipped: bool = True) -> io.BytesIO:
    body = "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
    return io.BytesIO(gzip.compress(body) if gzipped else body)


# ------------------------------------------------------------------- the format

print("export format")
source = fresh_db()
seed(source)
blob = dump(source)
check("output is gzip", blob[:2] == b"\x1f\x8b")
records = lines(blob)
header = records[0]
check("starts with a header", header["type"] == "header")
check("declares the format version", header["version"] == transfer.EXPORT_VERSION)
check("says whether candles are included", header["include_candles"] is False)
check("timestamps the export", header["exported_at"].endswith("Z"))
check("carries the status for the admin UI", header["status"]["macro_rows"] == 10)
by_type = counts(records)
check("one line per macro point", by_type["macro"] == 10)
check("one line per calibration row", by_type["calibration"] == 2)
check("one line per recommendation", by_type["recommendation"] == 12)
check("no candles unless asked", "candle" not in by_type)
check("macro lines are compact", set(records[1]) == {"type", "s", "d", "v"})
check("days are ISO dates", records[1]["d"] == "2026-07-01")
check("prices survive as strings", any(
    record.get("close_price") == "123.45" for record in records
))

status = transfer.dataset_status(source)
check("status counts the macro series", status["macro_series"] == 2)
check("status reports macro coverage", (status["macro_first_day"], status["macro_last_day"]) == ("2026-07-01", "2026-07-05"))
check("status counts calibrations", status["calibration_rows"] == 2)
check("status counts graded snapshots", status["recommendations_evaluated"] == 9)
check("status reports the snapshot window", status["recommendation_last_day"] == "2026-07-04")

with_candles = lines(dump(source, include_candles=True))
check("candles are included on request", counts(with_candles)["candle"] == 12)
check("the header says so", with_candles[0]["include_candles"] is True)
candle_record = next(record for record in with_candles if record["type"] == "candle")
check("candle lines are compact", set(candle_record) == {"type", "m", "d", "o", "h", "l", "c", "v"})

check("an empty database still exports a header", len(lines(dump(fresh_db()))) == 1)

# --------------------------------------------------------------- the round trip

print("round trip")
target = fresh_db()
result = transfer.import_dataset(target, io.BytesIO(dump(source, include_candles=True)))
check("reports the macro rows written", result["macro_rows"] == 10)
check("reports the macro records read", result["macro_records"] == 10)
check("reports the calibrations", result["calibration_rows"] == 2)
check("reports the snapshots", result["recommendation_rows"] == 12)
check("reports the candles", result["candle_rows"] == 12)
check("nothing was skipped", result["skipped_invalid"] == 0)

check("the dataset status matches the source", transfer.dataset_status(target) == transfer.dataset_status(source))

vix = target.scalars(
    select(OpusMacroSeries).where(OpusMacroSeries.series_id == "fred:vix").order_by(OpusMacroSeries.day)
).all()
check("macro values survive", [row.value for row in vix] == [15.0, 16.0, 17.0, 18.0, 19.0])
check("macro days survive", transfer._day_iso(vix[0].day) == "2026-07-01")

calibration = target.scalar(
    select(OpusCalibration).where(OpusCalibration.horizon == "1w")
)
payload = json.loads(calibration.payload)
check("the weight vector survives", payload["weights"] == {"mom_21": 0.4, "rev_5": -0.2})
check("nulls inside the payload survive", payload["alpha_bins"][1] is None)
check("the sample count survives", calibration.samples == 1234)
check("the calibration date survives", transfer._day_iso(calibration.calibrated_at) == "2026-07-06")

snapshot = target.scalar(
    select(OpusRecommendation).where(
        OpusRecommendation.market == "M1-EUR", OpusRecommendation.day == day_at(0)
    )
)
check("the action survives", snapshot.action == "hold")
check("scores survive", (snapshot.score, snapshot.buy_score) == (10.0, 20.0))
check("the euro figures survive", (snapshot.expected_return_pct, snapshot.net_edge_pct) == (1.5, 1.25))
check("the close price stays exact", snapshot.close_price == Decimal("124.45"))
check("the realized return survives", snapshot.realized_return_pct == 1.5)
check("the grading timestamp survives", transfer._day_iso(snapshot.evaluated_at) == "2026-07-06")

ungraded = target.scalar(
    select(OpusRecommendation).where(
        OpusRecommendation.market == "M1-EUR", OpusRecommendation.day == day_at(3)
    )
)
check("an ungraded snapshot stays ungraded", ungraded.realized_return_pct is None and ungraded.evaluated_at is None)

candle = target.scalar(select(MarketCandle).where(MarketCandle.market == "M2-EUR").order_by(MarketCandle.day))
check("candles land in market_candles", candle is not None and candle.close == Decimal("105.5"))
check("candle days survive", transfer._day_iso(candle.day) == "2026-07-01")

print("re-import is idempotent")
again = transfer.import_dataset(target, io.BytesIO(dump(source, include_candles=True)))
check("unchanged macro points are not rewritten", again["macro_rows"] == 0)
check("but they are still read", again["macro_records"] == 10)
check("no duplicate macro rows", target.scalar(select(func.count(OpusMacroSeries.id))) == 10)
check("no duplicate calibrations", target.scalar(select(func.count(OpusCalibration.id))) == 2)
check("no duplicate snapshots", target.scalar(select(func.count(OpusRecommendation.id))) == 12)
check("no duplicate candles", target.scalar(select(func.count(MarketCandle.id))) == 12)

print("import updates in place")
source.scalars(select(OpusMacroSeries).where(OpusMacroSeries.series_id == "fred:vix")).first().value = 99.0
row = source.scalar(select(OpusRecommendation).where(OpusRecommendation.market == "M0-EUR"))
row.realized_return_pct = -2.25
row.action = "sell"
source.commit()
updated = transfer.import_dataset(target, io.BytesIO(dump(source)))
check("a changed macro value is written", updated["macro_rows"] == 1)
check("and read back", target.scalars(
    select(OpusMacroSeries).where(OpusMacroSeries.series_id == "fred:vix").order_by(OpusMacroSeries.day)
).first().value == 99.0)
check("a re-graded snapshot is overwritten", target.scalar(
    select(OpusRecommendation).where(
        OpusRecommendation.market == "M0-EUR", OpusRecommendation.day == day_at(0)
    )
).realized_return_pct == -2.25)

print("plain NDJSON is accepted")
plain = io.BytesIO(gzip.decompress(dump(source)))
plain_target = fresh_db()
plain_result = transfer.import_dataset(plain_target, plain)
check("uncompressed input imports", plain_result["macro_rows"] == 10)
check("with the same snapshot count", plain_result["recommendation_rows"] == 12)

# ------------------------------------------------------------------ large input

print("large dataset")
big = fresh_db()
rng = random.Random(11)
# Enough poorly compressible rows that the export has to flush several times.
macro_points = 60_000
big.execute(insert(OpusMacroSeries), [
    {
        "series_id": f"synthetic:{index % 40}",
        "day": day_at(index // 40),
        "value": rng.uniform(-1000, 1000),
    }
    for index in range(macro_points)
])
snapshot_rows = transfer.IMPORT_BATCH + 500
big.execute(insert(OpusRecommendation), [
    {
        "day": day_at(index // 100),
        "market": f"B{index % 100}-EUR",
        "horizon": "1w",
        "action": "hold",
        "direction": "neutral",
        "score": rng.uniform(-100, 100),
        "buy_score": 0.0,
        "sell_score": 0.0,
    }
    for index in range(snapshot_rows)
])
big.commit()

chunks = list(transfer.export_dataset(big))
big_blob = b"".join(chunks)
check("a large export streams in chunks", len(chunks) > 1)
check("every chunk is bounded", max(len(chunk) for chunk in chunks) <= 512 * 1024)
check("gzip shrinks the file", len(big_blob) < len(gzip.decompress(big_blob)) / 2)

big_target = fresh_db()
big_result = transfer.import_dataset(big_target, io.BytesIO(big_blob))
check("every macro row crosses over", big_result["macro_rows"] == macro_points)
check("every snapshot crosses over", big_result["recommendation_rows"] == snapshot_rows)
check("more rows than one batch", macro_points > transfer.IMPORT_BATCH)
check("the target holds them all", big_target.scalar(select(func.count(OpusMacroSeries.id))) == macro_points)
check("and the snapshots too", big_target.scalar(select(func.count(OpusRecommendation.id))) == snapshot_rows)
check("status agrees on both sides", transfer.dataset_status(big_target) == transfer.dataset_status(big))

# ----------------------------------------------------------------- error paths

print("bad input")


def import_error(name: str, stream, expected: type[Exception] = ValueError, needle: str = "") -> None:
    db = fresh_db()
    try:
        transfer.import_dataset(db, stream)
    except expected as exc:
        check(name, needle.lower() in str(exc).lower(), str(exc))
    except Exception as exc:  # noqa: BLE001 - the point is the type is wrong
        check(name, False, f"raised {type(exc).__name__}: {exc}")
    else:
        check(name, False, "no error raised")


import_error("an empty file is rejected", io.BytesIO(b""), needle="empty")
import_error("an empty gzip file is rejected", io.BytesIO(gzip.compress(b"")), needle="empty")
import_error(
    "a non-JSON first line is rejected",
    io.BytesIO(gzip.compress(b"not json\n")),
    needle="valid json",
)
import_error(
    "a file without a header is rejected",
    ndjson([{"type": "macro", "s": "fred:vix", "d": "2026-07-01", "v": 1.0}]),
    needle="header",
)
import_error(
    "a future format version is rejected",
    ndjson([{"type": "header", "version": transfer.EXPORT_VERSION + 1}]),
    needle="version",
)

good_header = {"type": "header", "version": transfer.EXPORT_VERSION}
db = fresh_db()
mixed = transfer.import_dataset(db, ndjson([
    good_header,
    {"type": "macro", "s": "fred:vix", "d": "2026-07-01", "v": 12.0},
    {"type": "macro", "s": "fred:vix", "d": "2026-07-02"},          # no value
    {"type": "macro", "s": "fred:vix", "d": "2026-07-03", "v": "x"},  # unparsable
    {"type": "wat", "hello": 1},                                    # unknown type
    ["not", "an", "object"],                                        # not a record
    {"type": "recommendation", "day": "2026-07-01"},                # missing keys
]))
check("good rows still import", mixed["macro_rows"] == 1)
check("bad rows are counted, not fatal", mixed["skipped_invalid"] == 5)
check("only the good row is stored", db.scalar(select(func.count(OpusMacroSeries.id))) == 1)

broken = io.BytesIO(gzip.compress(
    json.dumps(good_header).encode() + b"\nthis line is not json\n"
    + json.dumps({"type": "macro", "s": "fred:vix", "d": "2026-07-02", "v": 13.0}).encode() + b"\n"
))
db = fresh_db()
salvaged = transfer.import_dataset(db, broken)
check("a corrupt line does not stop the import", salvaged["macro_rows"] == 1)
check("and is reported", salvaged["skipped_invalid"] == 1)

truncated = io.BytesIO(big_blob[: len(big_blob) // 2])
db = fresh_db()
try:
    transfer.import_dataset(db, truncated)
    check("a truncated file raises", False, "no error raised")
except (EOFError, gzip.BadGzipFile, ValueError):
    check("a truncated file raises", True)
    check("with rows before the cut committed", db.scalar(select(func.count(OpusMacroSeries.id))) > 0)
except Exception as exc:  # noqa: BLE001
    check("a truncated file raises", False, f"raised {type(exc).__name__}: {exc}")

header_only = io.BytesIO(gzip.compress(json.dumps(good_header).encode() + b"\n"))
db = fresh_db()
empty_result = transfer.import_dataset(db, header_only)
check("a header-only file imports nothing", empty_result["macro_rows"] == 0)
check("without an error", empty_result["skipped_invalid"] == 0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
