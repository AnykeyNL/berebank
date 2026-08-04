"""Standalone verification of candle history paging.

Run: .venv\\Scripts\\python test_candles_paging.py
"""
import inspect
from decimal import Decimal

from app.services import twelvedata

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


# 2025-01-01T00:00:00Z = 1735689600000; the 15m bar before it opens at 23:45.
MIDNIGHT_MS = 1735689600000
QUARTER_TO_MS = 1735688700000

ROWS = [
    {"datetime": "2025-01-01 00:00:00", "open": "1", "high": "2",
     "low": "0.5", "close": "1.5", "volume": "10"},
    {"datetime": "2024-12-31 23:45:00", "open": "1", "high": "2",
     "low": "0.5", "close": "1.5", "volume": "11"},
]

print("_end_date_param")
check(
    "steps back one second from the exclusive bound",
    twelvedata._end_date_param(MIDNIGHT_MS) == "2024-12-31 23:59:59",
    f"got {twelvedata._end_date_param(MIDNIGHT_MS)!r}",
)

print("_rows_to_candles")
out = twelvedata._rows_to_candles(ROWS, Decimal("1"))
check("oldest first", [c[0] for c in out] == [QUARTER_TO_MS, MIDNIGHT_MS],
      f"got {[c[0] for c in out]}")
check("volume preserved", out[0][5] == "11", f"got {out[0][5]!r}")
check("applies the fx rate",
      twelvedata._rows_to_candles(ROWS, Decimal("2"))[0][4] == "3.0",
      f"got {twelvedata._rows_to_candles(ROWS, Decimal('2'))[0][4]!r}")

bounded = twelvedata._rows_to_candles(ROWS, Decimal("1"), end_ms=MIDNIGHT_MS)
check("drops the inclusive boundary bar",
      [c[0] for c in bounded] == [QUARTER_TO_MS], f"got {[c[0] for c in bounded]}")
check("date-only rows parse",
      twelvedata._rows_to_candles(
          [{"datetime": "2024-12-31", "open": "1", "high": "1",
            "low": "1", "close": "1"}], Decimal("1"),
      )[0][5] == "0",
      "volume should default to 0")

print("fetch_candles signature")
sig = inspect.signature(twelvedata.TwelveDataService.fetch_candles)
check("accepts end_ms", "end_ms" in sig.parameters)
check("end_ms defaults to None", sig.parameters["end_ms"].default is None)

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
