"""Standalone verification of candle history paging.

Run: .venv\\Scripts\\python test_candles_paging.py
"""
import asyncio
import inspect
from decimal import Decimal

from app.routers import markets
from app.services import twelvedata
from app.services.bitvavo import bitvavo_service

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

print("cache keys and TTL")
check("live key has an empty end segment",
      markets._candle_cache_key("BTC-EUR", "1d", None) == "BTC-EUR:1d:",
      f"got {markets._candle_cache_key('BTC-EUR', '1d', None)!r}")
check("paged key carries the bound",
      markets._candle_cache_key("BTC-EUR", "1d", MIDNIGHT_MS) == f"BTC-EUR:1d:{MIDNIGHT_MS}")
check("live and paged keys differ",
      markets._candle_cache_key("BTC-EUR", "1d", None)
      != markets._candle_cache_key("BTC-EUR", "1d", MIDNIGHT_MS))
check("live TTL is the short one", markets._candle_cache_ttl(None) == markets._CANDLE_TTL)
check("paged TTL is the long one",
      markets._candle_cache_ttl(MIDNIGHT_MS) == markets._CANDLE_HISTORY_TTL)
check("paged TTL is longer than live",
      markets._CANDLE_HISTORY_TTL > markets._CANDLE_TTL)

print("cache stays bounded")
markets._candle_cache.clear()
for i in range(markets._CANDLE_CACHE_MAX + 50):
    markets._store_candles(f"BTC-EUR:1d:{i}", [])
check("bounded to the maximum",
      len(markets._candle_cache) == markets._CANDLE_CACHE_MAX,
      f"got {len(markets._candle_cache)}")
check("oldest entry evicted", "BTC-EUR:1d:0" not in markets._candle_cache)
check("newest entry kept",
      f"BTC-EUR:1d:{markets._CANDLE_CACHE_MAX + 49}" in markets._candle_cache)

print("endpoint forwards the bound")
bitvavo_service.markets["TEST-EUR"] = {"base": "TEST", "quote": "EUR", "market": "TEST-EUR"}
calls = []


async def fake_bitvavo(market, interval, limit, end=None):
    calls.append({"market": market, "interval": interval, "limit": limit, "end": end})
    base = end if end is not None else MIDNIGHT_MS
    return [[base - (limit - i) * 900_000, "1", "1", "1", "1", "1"] for i in range(limit)]


markets._fetch_bitvavo_candles = fake_bitvavo
markets._candle_cache.clear()

live = asyncio.run(markets.get_candles("test-eur", user=None, range_="1d"))
check("no end means no bound forwarded", calls[-1]["end"] is None)
check("preset interval and limit used",
      (calls[-1]["interval"], calls[-1]["limit"]) == markets._RANGE_PARAMS["1d"])
check("market upper-cased", calls[-1]["market"] == "TEST-EUR")
check("live page returned", len(live) == markets._RANGE_PARAMS["1d"][1])

page = asyncio.run(markets.get_candles("TEST-EUR", user=None, range_="1d", end=QUARTER_TO_MS))
check("end forwarded unmodified", calls[-1]["end"] == QUARTER_TO_MS)
check("page is strictly older than the bound",
      all(c[0] < QUARTER_TO_MS for c in page), f"got {[c[0] for c in page][-1:]}")
check("live and paged responses cached separately", len(markets._candle_cache) == 2)

before = len(calls)
asyncio.run(markets.get_candles("TEST-EUR", user=None, range_="1d", end=QUARTER_TO_MS))
check("second paged request served from cache", len(calls) == before)

print("endpoint validation")
try:
    asyncio.run(markets.get_candles("TEST-EUR", user=None, range_="7d"))
    check("rejects an unknown range", False, "(no error raised)")
except Exception as exc:
    check("rejects an unknown range", "Invalid range" in str(exc), f"got {exc}")

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
