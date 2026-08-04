# Chart Zoom History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zooming out on the trade and analyze charts pages in older bars at the same interval, up to 10x the preset window, without changing the default view.

**Architecture:** The candles endpoint gains an optional exclusive `end` timestamp that returns the page of bars just before it, at the preset's interval and bar count. A new frontend module accumulates those pages and exposes them to both charts, which trigger a fetch when the viewport nears the loaded left edge and preserve the viewport (captured as timestamps) whenever the data array is replaced.

**Tech Stack:** FastAPI + httpx (backend), React 19 + TypeScript + lightweight-charts v5 (frontend), vitest + @testing-library/react (frontend tests), standalone `python test_*.py` scripts (backend tests).

**Spec:** `docs/superpowers/specs/2026-08-04-chart-zoom-history-design.md`

## Global Constraints

- Bar interval **never** changes with zoom — always the preset's interval from `_RANGE_PARAMS`.
- `end` is **epoch milliseconds and exclusive**: every returned bar satisfies `timestamp_ms < end`.
- Omitting `end` must reproduce today's response byte-for-byte; the MCP `get_candles` tool is not changed.
- History depth cap: base page + **9** extra pages (`MAX_HISTORY_PAGES = 9`), i.e. 10x the preset window.
- Numbers around the charts stay tied to the **selected preset**, not the visible window.
- Candle shape stays `[timestamp_ms, open, high, low, close, volume]`, oldest first, decimals as strings.
- Backend tests are standalone scripts (`check()` helper, `raise SystemExit(1 if failed else 0)`), run as `.venv\Scripts\python test_x.py` from `backend/`.
- Every new UI string is added to **both** `frontend/src/locales/en.json` and `nl.json`.
- Working directory for backend commands is `c:\projects\berebank\backend`; for frontend commands `c:\projects\berebank\frontend`. The shell is PowerShell — chain commands with `;`, not `&&`, and use `curl.exe` rather than `curl`.

---

### Task 1: Twelve Data backward window

Adds an exclusive `end_ms` bound to the Twelve Data candle fetch, for stocks, funds and commodities. Pure helpers do the date formatting and the row conversion so both are testable without HTTP.

**Files:**
- Modify: `backend/app/services/twelvedata.py` (add two module-level helpers after `_listing_from_quote` at line 41-46; change `fetch_candles` at lines 255-298)
- Test: `backend/test_candles_paging.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `twelvedata._end_date_param(end_ms: int) -> str`
  - `twelvedata._rows_to_candles(rows: list[dict], fx: Decimal, end_ms: int | None = None) -> list[list]`
  - `TwelveDataService.fetch_candles(market: str, range_: str, extra_bars: int = 0, end_ms: int | None = None) -> list[list]`

- [ ] **Step 1: Write the failing test**

Create `backend/test_candles_paging.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`: `.venv\Scripts\python test_candles_paging.py`
Expected: `AttributeError: module 'app.services.twelvedata' has no attribute '_end_date_param'`

- [ ] **Step 3: Write the implementation**

In `backend/app/services/twelvedata.py`, add after `_listing_from_quote` (which ends at line 46):

```python
def _end_date_param(end_ms: int) -> str:
    """Twelve Data ``end_date`` for an exclusive epoch-ms bound.

    Their bound is inclusive, so step back a second; bars landing exactly on
    the boundary are dropped in ``_rows_to_candles``.
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(end_ms // 1000 - 1))


def _rows_to_candles(rows: list[dict], fx: Decimal, end_ms: int | None = None) -> list[list]:
    """time_series rows → API-shape candles in EUR, oldest first.

    Bars at or after ``end_ms`` are dropped so the exclusive bound holds even
    though Twelve Data's ``end_date`` is inclusive.
    """
    candles = []
    for row in rows:
        raw = row["datetime"]
        fmt = "%Y-%m-%d %H:%M:%S" if " " in raw else "%Y-%m-%d"
        ts = calendar.timegm(time.strptime(raw[:19], fmt)) * 1000  # datetimes are UTC
        if end_ms is not None and ts >= end_ms:
            continue
        candles.append([
            int(ts),
            str(_dec(row["open"]) * fx),
            str(_dec(row["high"]) * fx),
            str(_dec(row["low"]) * fx),
            str(_dec(row["close"]) * fx),
            row.get("volume", "0"),
        ])
    candles.sort(key=lambda c: c[0])
    return candles
```

Then replace `fetch_candles` (lines 255-298) with:

```python
    async def fetch_candles(
        self,
        market: str,
        range_: str,
        extra_bars: int = 0,
        end_ms: int | None = None,
    ) -> list[list]:
        """OHLCV candles as [timestamp_ms, open, high, low, close, volume],
        oldest first, converted to EUR — same shape as the Bitvavo candles.

        ``extra_bars`` extends the window backwards at the same interval
        (used as indicator warm-up by the analysis endpoint). ``end_ms``
        (epoch ms, exclusive) returns the page of bars just before it."""
        inst = self._instruments.get(market)
        if inst is None:
            raise RuntimeError(f"Unknown Twelve Data market: {market}")
        if self.api_key is None:
            raise RuntimeError("Twelve Data API key not configured")
        interval, outputsize = self._RANGE_PARAMS[range_]
        outputsize += extra_bars
        params = {
            "symbol": inst.td_symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",
            "apikey": self.api_key,
        }
        if end_ms is not None:
            params["end_date"] = _end_date_param(end_ms)
        async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as client:
            resp = await client.get("/time_series", params=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            raise RuntimeError(f"time_series error: {data.get('message', data)}")

        fx = self.usd_eur if inst.currency == "USD" else Decimal("1")
        if fx is None:
            raise RuntimeError("USD/EUR rate not available yet")
        return _rows_to_candles(data["values"], fx, end_ms)
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`: `.venv\Scripts\python test_candles_paging.py`
Expected: `8 passed, 0 failed`

- [ ] **Step 5: Verify the analysis path still works**

Run from `backend/`: `.venv\Scripts\python test_analysis.py`
Expected: the existing `N passed, 0 failed` (the analysis service is unchanged; this confirms the refactor did not break candle shapes it consumes).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/twelvedata.py backend/test_candles_paging.py
git commit -m "Add exclusive end bound to Twelve Data candle fetch"
```

---

### Task 2: Candles endpoint `end` parameter

Exposes the backward window on the API, forwards it to Bitvavo (whose `end` is already exclusive) and to Task 1's `end_ms`, and keeps the response cache correct and bounded now that keys include a timestamp.

**Files:**
- Modify: `backend/app/routers/markets.py` (constants at lines 66-79; `_fetch_bitvavo_candles` at lines 93-101; `get_candles` at lines 477-511)
- Modify: `docs/marketdata.md` (lines 139-155)
- Test: `backend/test_candles_paging.py` (extend)

**Interfaces:**
- Consumes: `TwelveDataService.fetch_candles(market, range_, extra_bars=0, end_ms=None)` from Task 1.
- Produces:
  - `GET /markets/{market}/candles?range=<preset>&end=<epoch_ms>` returning `list[list]`, oldest first, all bars `< end`
  - `markets._candle_cache_key(market: str, range_: str, end: int | None) -> str`
  - `markets._candle_cache_ttl(end: int | None) -> int`
  - `markets._store_candles(key: str, candles: list) -> None`
  - `markets._fetch_bitvavo_candles(market, interval, limit, end: int | None = None)`

- [ ] **Step 1: Write the failing test**

Append to `backend/test_candles_paging.py`, **before** the closing `print()`/`SystemExit` lines (move those two blocks to the end of the file):

```python
import asyncio

from app.routers import markets
from app.services.bitvavo import bitvavo_service

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`: `.venv\Scripts\python test_candles_paging.py`
Expected: `AttributeError: module 'app.routers.markets' has no attribute '_candle_cache_key'`

- [ ] **Step 3: Write the implementation**

In `backend/app/routers/markets.py`, replace the `_CANDLE_TTL` line (line 66) with:

```python
_CANDLE_TTL = 60  # seconds
_CANDLE_HISTORY_TTL = 3600  # seconds; pages behind the live window never change
_CANDLE_CACHE_MAX = 500  # bound the cache: paged keys carry unbounded timestamps
```

Replace `_fetch_bitvavo_candles` (lines 93-101) with:

```python
async def _fetch_bitvavo_candles(
    market: str, interval: str, limit: int, end: int | None = None
) -> list:
    params: dict[str, object] = {"interval": interval, "limit": limit}
    if end is not None:
        # Bitvavo's `end` is exclusive, matching our own bound.
        params["end"] = end
    async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
        resp = await client.get(f"/{market}/candles", params=params)
        if resp.status_code != 200:
            raise HTTPException(502, "Could not fetch candles from Bitvavo")
        return sorted(resp.json(), key=lambda c: c[0])


def _candle_cache_key(market: str, range_: str, end: int | None) -> str:
    return f"{market}:{range_}:{end or ''}"


def _candle_cache_ttl(end: int | None) -> int:
    """Pages behind the live window are immutable, so they can be held longer."""
    return _CANDLE_HISTORY_TTL if end else _CANDLE_TTL


def _store_candles(key: str, candles: list) -> None:
    _candle_cache[key] = (time.monotonic(), candles)
    while len(_candle_cache) > _CANDLE_CACHE_MAX:
        _candle_cache.pop(next(iter(_candle_cache)))
```

Replace `get_candles` (lines 477-511) with:

```python
@router.get("/{market}/candles")
async def get_candles(
    market: str,
    user: User = Depends(get_current_user),
    range_: Annotated[str, Query(alias="range")] = "1d",
    end: Annotated[int | None, Query(ge=0)] = None,
):
    """OHLCV candles from Bitvavo for the requested range (oldest first).

    Each candle is [timestamp_ms, open, high, low, close, volume].
    Ranges: 1h, 1d, 1w, 30d, 90d, 180d, 365d.

    ``end`` (epoch ms, exclusive) returns the page of bars just before it at
    the range's own interval, so charts can extend history on zoom out.
    """
    market = market.upper()
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise HTTPException(404, f"Unknown market: {market}")

    if range_ not in _RANGE_PARAMS:
        raise HTTPException(400, f"Invalid range: {range_}. Use one of {', '.join(_RANGE_PARAMS)}")

    cache_key = _candle_cache_key(market, range_, end)
    cached = _candle_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _candle_cache_ttl(end):
        return cached[1]

    if market_info["asset_class"] == "crypto":
        interval, limit = _RANGE_PARAMS[range_]
        candles = await _fetch_bitvavo_candles(market, interval, limit, end=end)
    else:
        try:
            candles = await twelvedata_service.fetch_candles(market, range_, end_ms=end)
        except Exception as exc:
            raise HTTPException(502, f"Could not fetch candles from Twelve Data: {exc}")

    _store_candles(cache_key, candles)
    return candles
```

Note the analysis endpoint at line 548-552 calls `_fetch_bitvavo_candles(market, interval, display_count + WARMUP_BARS)` positionally — the new `end` parameter is keyword-with-default, so that call is unaffected. Leave it alone.

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`: `.venv\Scripts\python test_candles_paging.py`
Expected: `26 passed, 0 failed`

- [ ] **Step 5: Update the market data doc**

In `docs/marketdata.md`, replace the heading and the Cache line of that section (lines 139 and 155):

Change line 139 from `### Live candles — \`GET /markets/{market}/candles?range=\`` to:

```markdown
### Live candles — `GET /markets/{market}/candles?range=&end=`
```

Change line 155 from the single Cache line to:

```markdown
**Paging (`end`):** optional epoch-ms bound, **exclusive**. Returns the page of
bars just before `end` at the same interval and bar count as the range, so
charts can extend history when the user zooms out. Bitvavo takes `end`
directly; Twelve Data gets `end_date` (inclusive) and the boundary bar is
dropped. Omitting `end` is unchanged behaviour.

**Cache:** Per-market candle + analysis responses cached **60 seconds**; candle
pages with `end` set are immutable and cached **1 hour**, with the candle cache
bounded to 500 entries.
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/markets.py backend/test_candles_paging.py docs/marketdata.md
git commit -m "Serve older candle pages via an exclusive end bound"
```

---

### Task 3: Frontend history paging module

The single place that knows about extra history: fetching a page, accumulating pages, the depth cap, the viewport-preservation rule, and the viewport subscription. Both charts consume it, so neither grows much.

**Files:**
- Create: `frontend/src/lib/chartHistory.ts`
- Create: `frontend/src/lib/chartHistory.test.ts`

**Interfaces:**
- Consumes: `GET /markets/{market}/candles?range=&end=` from Task 2; `api` from `frontend/src/lib/api.ts`; `Candle` from `frontend/src/lib/types.ts` (`[number, string, string, string, string, string]`).
- Produces:
  - `MAX_HISTORY_PAGES = 9`, `LOAD_THRESHOLD_BARS = 10`
  - `fetchCandlePage(market: string, range: string, end?: number, signal?: AbortSignal): Promise<Candle[]>`
  - `olderThan(page: Candle[], ts: number): Candle[]`
  - `shouldFitContent(olderCount: number, logicalRange: { from: number; to: number } | null, barCount: number): boolean`
  - `useOlderHistory({ market, range, baseBars }): { bars: Candle[]; olderCount: number; loadOlder: () => void; loading: boolean; canLoadMore: boolean }`
  - `attachHistoryTrigger(chart: IChartApi, canLoad: () => boolean, loadOlder: () => void): () => void`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/chartHistory.test.ts`:

```ts
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import type { Candle } from './types'
import {
  MAX_HISTORY_PAGES,
  olderThan,
  shouldFitContent,
  useOlderHistory,
} from './chartHistory'

vi.mock('./api', () => ({ api: vi.fn() }))

const mockedApi = vi.mocked(api)

const MINUTE = 60_000

/** `count` bars ending just before `end`, one minute apart. */
function page(end: number, count = 3): Candle[] {
  return Array.from({ length: count }, (_, i) => {
    const ts = end - (count - i) * MINUTE
    return [ts, '1', '1', '1', '1', '1'] as Candle
  })
}

const BASE_END = 1_000 * MINUTE
const baseBars = page(BASE_END, 3)

beforeEach(() => {
  mockedApi.mockReset()
})

describe('olderThan', () => {
  it('keeps only bars strictly older than the bound', () => {
    const bars = page(BASE_END, 3)
    expect(olderThan(bars, bars[1][0])).toEqual([bars[0]])
  })

  it('drops everything at or after the bound', () => {
    const bars = page(BASE_END, 3)
    expect(olderThan(bars, bars[0][0])).toEqual([])
  })
})

describe('shouldFitContent', () => {
  it('fits while the viewport matches the full dataset', () => {
    expect(shouldFitContent(0, { from: 0, to: 99 }, 100)).toBe(true)
  })

  it('fits when there is no viewport yet', () => {
    expect(shouldFitContent(0, null, 100)).toBe(true)
  })

  it('preserves the viewport when zoomed in', () => {
    expect(shouldFitContent(0, { from: 40, to: 60 }, 100)).toBe(false)
  })

  it('preserves the viewport when zoomed out past both ends', () => {
    expect(shouldFitContent(0, { from: -50, to: 150 }, 100)).toBe(false)
  })

  it('preserves the viewport once older pages are loaded', () => {
    expect(shouldFitContent(1, { from: 0, to: 99 }, 100)).toBe(false)
  })
})

describe('useOlderHistory', () => {
  it('returns the base bars untouched before any paging', () => {
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))
    expect(result.current.bars).toEqual(baseBars)
    expect(result.current.olderCount).toBe(0)
    expect(mockedApi).not.toHaveBeenCalled()
  })

  it('requests the page before the oldest loaded bar', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())

    await waitFor(() => expect(result.current.olderCount).toBe(3))
    expect(mockedApi.mock.calls[0][0]).toBe(
      `/markets/BTC-EUR/candles?range=1d&end=${baseBars[0][0]}`,
    )
    expect(result.current.bars).toHaveLength(6)
    expect(result.current.bars[0][0]).toBeLessThan(baseBars[0][0])
    expect(result.current.bars.at(-1)).toEqual(baseBars.at(-1))
  })

  it('stops asking once a page comes back empty', async () => {
    mockedApi.mockResolvedValue([])
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.canLoadMore).toBe(false))

    act(() => result.current.loadOlder())
    expect(mockedApi).toHaveBeenCalledTimes(1)
  })

  it('issues one request when triggered twice in a row', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => {
      result.current.loadOlder()
      result.current.loadOlder()
    })

    await waitFor(() => expect(result.current.olderCount).toBe(3))
    expect(mockedApi).toHaveBeenCalledTimes(1)
  })

  it('stops at the page cap', async () => {
    mockedApi.mockImplementation((path: string) => {
      const end = Number(new URL(path, 'http://x').searchParams.get('end'))
      return Promise.resolve(page(end, 3))
    })
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    for (let i = 0; i < MAX_HISTORY_PAGES + 3; i++) {
      act(() => result.current.loadOlder())
      await waitFor(() => expect(result.current.loading).toBe(false))
    }

    expect(mockedApi).toHaveBeenCalledTimes(MAX_HISTORY_PAGES)
    expect(result.current.canLoadMore).toBe(false)
    expect(result.current.olderCount).toBe(MAX_HISTORY_PAGES * 3)
  })

  it('discards loaded pages when the range changes', async () => {
    mockedApi.mockResolvedValue(page(baseBars[0][0], 3))
    const { result, rerender } = renderHook(
      ({ range }) => useOlderHistory({ market: 'BTC-EUR', range, baseBars }),
      { initialProps: { range: '1d' } },
    )

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.olderCount).toBe(3))

    rerender({ range: '1w' })
    expect(result.current.olderCount).toBe(0)
    expect(result.current.bars).toEqual(baseBars)
  })

  it('keeps the loaded pages when a page request fails', async () => {
    mockedApi.mockRejectedValue(new Error('502'))
    const { result } = renderHook(() => useOlderHistory({ market: 'BTC-EUR', range: '1d', baseBars }))

    act(() => result.current.loadOlder())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.bars).toEqual(baseBars)
    expect(result.current.canLoadMore).toBe(true)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `frontend/`: `npx vitest run src/lib/chartHistory.test.ts`
Expected: FAIL — `Failed to resolve import "./chartHistory"`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/chartHistory.ts`:

```ts
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { IChartApi, LogicalRange } from 'lightweight-charts'
import { api } from './api'
import type { Candle } from './types'

/** Pages of older bars a chart may add on top of its preset window. */
export const MAX_HISTORY_PAGES = 9

/** Fetch the next page once the viewport comes this close to the left edge. */
export const LOAD_THRESHOLD_BARS = 10

/** Bars within this many logical units of a perfect fit still count as fitted. */
const FIT_TOLERANCE = 1

export async function fetchCandlePage(
  market: string,
  range: string,
  end?: number,
  signal?: AbortSignal,
): Promise<Candle[]> {
  const params = new URLSearchParams({ range })
  if (end !== undefined) params.set('end', String(end))
  return api<Candle[]>(`/markets/${encodeURIComponent(market)}/candles?${params}`, { signal })
}

/** The API bound is exclusive; filter anyway so a stray bar cannot duplicate. */
export function olderThan(page: Candle[], ts: number): Candle[] {
  return page.filter((c) => c[0] < ts)
}

/**
 * Whether the chart should keep auto-following the newest bar. True only while
 * it is still in the fitted state: no extra history and a viewport that spans
 * exactly the dataset. Zooming in raises `from` and lowers `to`; zooming out
 * pushes both past the ends — either way the viewport is worth preserving.
 */
export function shouldFitContent(
  olderCount: number,
  logicalRange: { from: number; to: number } | null,
  barCount: number,
): boolean {
  if (olderCount > 0) return false
  if (logicalRange === null) return true
  return (
    Math.abs(logicalRange.from) <= FIT_TOLERANCE &&
    Math.abs(logicalRange.to - (barCount - 1)) <= FIT_TOLERANCE
  )
}

/**
 * Accumulates pages of older bars in front of a chart's preset window.
 *
 * The caller owns `baseBars` (its own fetch or payload); this hook only ever
 * adds history in front of them, and throws its pages away when the market or
 * range changes.
 */
export function useOlderHistory({
  market,
  range,
  baseBars,
}: {
  market: string
  range: string
  baseBars: Candle[] | null
}) {
  const [older, setOlder] = useState<Candle[]>([])
  const [pages, setPages] = useState(0)
  const [exhausted, setExhausted] = useState(false)
  const [loading, setLoading] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef(false)
  const oldestRef = useRef<number | null>(null)

  useEffect(() => {
    abortRef.current?.abort()
    abortRef.current = null
    inFlightRef.current = false
    setOlder([])
    setPages(0)
    setExhausted(false)
    setLoading(false)
  }, [market, range])

  useEffect(() => () => abortRef.current?.abort(), [])

  const bars = useMemo(() => {
    const merged = baseBars && baseBars.length > 0 ? [...older, ...baseBars] : older
    oldestRef.current = merged.length > 0 ? merged[0][0] : null
    return merged
  }, [older, baseBars])

  const canLoadMore = !exhausted && pages < MAX_HISTORY_PAGES && bars.length > 0

  const loadOlder = useCallback(() => {
    if (inFlightRef.current || exhausted || pages >= MAX_HISTORY_PAGES) return
    const oldest = oldestRef.current
    if (oldest === null) return

    inFlightRef.current = true
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller

    fetchCandlePage(market, range, oldest, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return
        const fresh = olderThan(page, oldest)
        if (fresh.length === 0) {
          setExhausted(true)
          return
        }
        setPages((n) => n + 1)
        setOlder((current) => [...fresh, ...current])
      })
      .catch(() => {
        // Keep what is loaded; the next viewport change retries.
      })
      .finally(() => {
        if (controller.signal.aborted) return
        inFlightRef.current = false
        setLoading(false)
      })
  }, [market, range, exhausted, pages])

  return { bars, olderCount: older.length, loadOlder, loading, canLoadMore }
}

/**
 * Call `loadOlder` when the viewport nears the left edge of the loaded bars.
 * Returns an unsubscribe function.
 */
export function attachHistoryTrigger(
  chart: IChartApi,
  canLoad: () => boolean,
  loadOlder: () => void,
): () => void {
  const handler = (logicalRange: LogicalRange | null) => {
    if (logicalRange === null) return
    if (logicalRange.from > LOAD_THRESHOLD_BARS) return
    if (!canLoad()) return
    loadOlder()
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(handler)
  return () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler)
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `frontend/`: `npx vitest run src/lib/chartHistory.test.ts`
Expected: PASS, 14 tests.

If the cap test fails because `loadOlder` is stale between iterations, that is the bug the test is for: `loadOlder`'s dependency array must include `pages` and `exhausted` (it does above) so each `act` call sees current state.

- [ ] **Step 5: Typecheck and lint**

Run from `frontend/`: `npx tsc -b; npx oxlint src/lib/chartHistory.ts`
Expected: no errors. If `LogicalRange` is not exported by `lightweight-charts` v5, change the handler parameter type to `{ from: number; to: number } | null` and drop that import.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/chartHistory.ts frontend/src/lib/chartHistory.test.ts
git commit -m "Add chart history paging module"
```

---

### Task 4: Wire the trade page price chart

Feeds `PriceChart` the extended bars, keeps its badge and footer on the preset window, and stops `fitContent()` from wiping the viewport on every 60-second poll.

**Note on TDD:** the module logic this task consumes is fully tested in Task 3. This task is chart wiring against a canvas renderer that jsdom cannot meaningfully assert on, so it is verified by typecheck, build, the existing test suite, and the manual checks in Step 6 — no new unit test. Do not add a canvas-mocking test to satisfy the letter of TDD.

**Files:**
- Modify: `frontend/src/components/PriceChart.tsx` (imports at lines 1-22; state at lines 104-118; fetch effect at lines 120-144; memos at lines 146-166; series effect at lines 269-347; markup at lines 410-425)
- Modify: `frontend/src/locales/en.json` (chart block, lines 279-301)
- Modify: `frontend/src/locales/nl.json` (chart block, lines 279-301)

**Interfaces:**
- Consumes: `useOlderHistory`, `attachHistoryTrigger`, `shouldFitContent` from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the translation strings**

In `frontend/src/locales/en.json`, inside the `chart` block, after `"loadError"` (line 293):

```json
    "loadingHistory": "Loading older data…",
```

In `frontend/src/locales/nl.json`, the same position:

```json
    "loadingHistory": "Oudere gegevens laden…",
```

- [ ] **Step 2: Replace the local Candle type with the shared one**

In `frontend/src/components/PriceChart.tsx`, delete lines 21-22:

```ts
// Bitvavo candle: [timestamp_ms, open, high, low, close, volume]
type Candle = [number, string, string, string, string, string]
```

and extend the imports below line 19 with:

```ts
import { attachHistoryTrigger, shouldFitContent, useOlderHistory } from '../lib/chartHistory'
import type { Candle } from '../lib/types'
```

- [ ] **Step 3: Rename the fetched state to `baseCandles` and add the hook**

Replace line 104:

```ts
  const [baseCandles, setBaseCandles] = useState<Candle[] | null>(null)
```

In the fetch effect (lines 120-144) replace `setCandles(null)` with `setBaseCandles(null)` and `setCandles(data)` with `setBaseCandles(data)`.

Immediately after that effect, add:

```ts
  const { bars, olderCount, loadOlder, loading: loadingHistory, canLoadMore } = useOlderHistory({
    market,
    range,
    baseBars: baseCandles,
  })
  const candles = bars.length > 0 ? bars : null
```

`candles` keeps the rest of the component reading the extended array under its existing name, so the series effect, trade markers and the loading skeleton need no further edits.

- [ ] **Step 4: Keep the badge and footer on the preset window**

Replace the `stats` memo (lines 154-166) with:

```ts
  // Preset-scoped: the 1D badge stays the 1D change however far the user zooms.
  const stats = useMemo(() => {
    if (!baseCandles || baseCandles.length < 2) return null
    const closes = baseCandles.map((c) => parseFloat(c[4]))
    const lows = baseCandles.map((c) => parseFloat(c[3]))
    const highs = baseCandles.map((c) => parseFloat(c[2]))
    const min = Math.min(...lows)
    const max = Math.max(...highs)
    const first = closes[0]
    const lastClose = closes[closes.length - 1]
    const changePct = first !== 0 ? ((lastClose - first) / first) * 100 : null
    const up = lastClose >= first
    return { min, max, changePct, up, lastClose }
  }, [baseCandles])
```

- [ ] **Step 5: Preserve the viewport and subscribe to the trigger**

Add these refs **immediately after the `useOlderHistory` block from Step 3** — they
read `loadOlder`, `canLoadMore` and `loadingHistory`, so they cannot sit with the
other refs at line 118, which runs before the hook is declared:

```ts
  const renderedCountRef = useRef(0)
  const renderedOlderRef = useRef(0)
  const loadOlderRef = useRef(loadOlder)
  const canLoadRef = useRef(canLoadMore)
  loadOlderRef.current = loadOlder
  canLoadRef.current = canLoadMore && !loadingHistory
```

After the chart-creation effect (which ends at line 256), add:

```ts
  // Page in older bars when the viewport reaches the left edge.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    return attachHistoryTrigger(
      chart,
      () => canLoadRef.current,
      () => loadOlderRef.current(),
    )
  }, [])
```

In the series effect, capture the viewport **before** the series is removed. Insert directly after `if (!chart) return` (line 271):

```ts
    const timeScale = chart.timeScale()
    const fit = shouldFitContent(
      renderedOlderRef.current,
      timeScale.getVisibleLogicalRange(),
      renderedCountRef.current,
    )
    const keptRange = fit ? null : timeScale.getVisibleRange()
```

and replace the effect's last statement, `chart.timeScale().fitContent()` (line 346), with:

```ts
    renderedCountRef.current = candles.length
    renderedOlderRef.current = olderCount
    if (keptRange === null) timeScale.fitContent()
    else timeScale.setVisibleRange(keptRange)
```

Add `olderCount` to that effect's dependency array (line 347):

```ts
  }, [candles, chartType, stats, limitOrders, tradeMarkers, autoscaleInfoProvider, lastPrice, olderCount, t])
```

- [ ] **Step 6: Show the paging indicator**

In the chart container (lines 410-425), add inside the `relative` wrapper, after the loading-skeleton block:

```tsx
        {loadingHistory && (
          <div className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-slate-900/80 px-2 py-1 text-[10px] text-slate-400">
            {t('chart.loadingHistory')}
          </div>
        )}
```

- [ ] **Step 7: Typecheck, lint and build**

Run from `frontend/`: `npx tsc -b; npx oxlint src; npm run build`
Expected: no type errors, no new lint warnings, successful build.

- [ ] **Step 8: Run the existing test suite**

Run from `frontend/`: `npx vitest run`
Expected: all suites pass (the two page tests plus `chartHistory`).

- [ ] **Step 9: Verify by hand**

Start the app, open a trade page for `BTC-EUR`, and confirm:
1. On load the chart shows the same 1D window as before, right-aligned on the newest bar.
2. Scrolling out pulls in older candles; the "Loading older data…" pill appears briefly.
3. The % change badge and the low/high footer do **not** change while zooming out.
4. Waiting past a 60-second refresh keeps the zoomed viewport instead of snapping back.
5. Switching range resets to the default view for that preset.
6. Repeat on a stock (`AAPL-EUR`) to exercise the Twelve Data path.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/PriceChart.tsx frontend/src/locales/en.json frontend/src/locales/nl.json
git commit -m "Load older bars when zooming out the trade chart"
```

---

### Task 5: Wire the analyze page chart

Same treatment for the analysis candlestick chart, whose base bars come from the analysis payload rather than its own fetch. Overlays and verdicts stay on the analysis window by design, so they simply end where the preset window starts.

**Note on TDD:** as in Task 4, the logic is covered by Task 3's tests and this is canvas wiring; verification is typecheck, build and the manual checks in Step 5.

**Files:**
- Modify: `frontend/src/pages/AnalyzePage.tsx` (imports at lines 1-29; `AnalysisChart` at lines 266-376; render site at line 600)

**Interfaces:**
- Consumes: `useOlderHistory`, `attachHistoryTrigger`, `shouldFitContent` from Task 3; `chart.loadingHistory` from Task 4.
- Produces: nothing.

- [ ] **Step 1: Extend the imports**

In `frontend/src/pages/AnalyzePage.tsx`, after line 17, add:

```ts
import { attachHistoryTrigger, shouldFitContent, useOlderHistory } from '../lib/chartHistory'
```

- [ ] **Step 2: Take the market and range as props and page history**

Replace the `AnalysisChart` signature and its first hook block (lines 266-269) with:

```tsx
function AnalysisChart({
  analysis,
  overlays,
  market,
  range,
}: {
  analysis: Analysis
  overlays: Set<Overlay>
  market: string
  range: AnalysisRange
}) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<SeriesType>[]>([])
  const renderedCountRef = useRef(0)
  const renderedOlderRef = useRef(0)

  const { bars, olderCount, loadOlder, loading: loadingHistory, canLoadMore } = useOlderHistory({
    market,
    range,
    baseBars: analysis.candles,
  })
  const loadOlderRef = useRef(loadOlder)
  const canLoadRef = useRef(canLoadMore)
  loadOlderRef.current = loadOlder
  canLoadRef.current = canLoadMore && !loadingHistory
```

- [ ] **Step 3: Subscribe to the trigger**

After the chart-creation effect (which ends at line 304 with `}, [])`), add:

```tsx
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    return attachHistoryTrigger(
      chart,
      () => canLoadRef.current,
      () => loadOlderRef.current(),
    )
  }, [])
```

- [ ] **Step 4: Draw the extended bars and keep the viewport**

In the data effect (lines 306-373):

Insert after `if (!chart) return` (line 308):

```tsx
    const timeScale = chart.timeScale()
    const fit = shouldFitContent(
      renderedOlderRef.current,
      timeScale.getVisibleLogicalRange(),
      renderedCountRef.current,
    )
    const keptRange = fit ? null : timeScale.getVisibleRange()
```

Replace lines 312-313:

```tsx
    const candles = bars
    if (candles.length < 2) return
```

Replace the final `chart.timeScale().fitContent()` (line 372) with:

```tsx
    renderedCountRef.current = candles.length
    renderedOlderRef.current = olderCount
    if (keptRange === null) timeScale.fitContent()
    else timeScale.setVisibleRange(keptRange)
```

Change the dependency array (line 373) to:

```tsx
  }, [analysis, overlays, bars, olderCount])
```

The overlay lines and level price lines keep reading `analysis.strategies` and `lastClose` from `candles[candles.length - 1]`, which is still the newest bar, so those blocks need no edit.

- [ ] **Step 5: Show the paging indicator and pass the new props**

Replace the component's return (line 375) with:

```tsx
  return (
    <div className="relative w-full">
      {loadingHistory && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-slate-900/80 px-2 py-1 text-[10px] text-slate-400">
          {t('chart.loadingHistory')}
        </div>
      )}
      <div ref={containerRef} className="w-full" />
    </div>
  )
```

Update the render site (line 600) to:

```tsx
          {!error && analysis && (
            <AnalysisChart analysis={analysis} overlays={overlays} market={market} range={range} />
          )}
```

- [ ] **Step 6: Typecheck, lint, build and test**

Run from `frontend/`: `npx tsc -b; npx oxlint src; npm run build; npx vitest run`
Expected: no errors, all suites pass.

- [ ] **Step 7: Verify by hand**

Open the analyze page for `BTC-EUR` and confirm:
1. The default 30D window is unchanged on load.
2. Zooming out adds older candles; the SMA/EMA/Bollinger overlays stop at the 30D boundary rather than being redrawn.
3. The strategy cards and verdicts do not change while zooming.
4. The viewport survives the 60-second analysis refresh.
5. Switching range resets to that preset's default view.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/AnalyzePage.tsx
git commit -m "Load older bars when zooming out the analyze chart"
```

---

## Verification

After all tasks, from `backend/`:

```
.venv\Scripts\python test_candles_paging.py
.venv\Scripts\python test_analysis.py
```

From `frontend/`:

```
npx vitest run
npm run build
```

All must pass with no new warnings, plus the manual browser checks in Tasks 4 and 5.
