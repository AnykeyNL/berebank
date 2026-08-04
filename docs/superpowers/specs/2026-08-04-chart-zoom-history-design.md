# Chart zoom loads more history — design

**Date:** 2026-08-04
**Status:** Approved

## Goal

The mouse wheel already zooms the price charts, but zooming out shows no extra
data: each chart holds exactly one fixed batch of bars (the trade chart's `1D`
preset is 96 fifteen-minute candles) and nothing listens for viewport changes.

Zooming out must pull in **older bars at the same interval**, up to a bounded
depth, while **the default view stays byte-for-byte what it is today**.

Scope: the **trade page price chart** (`PriceChart`) and the **analyze page
candlestick chart** (`AnalysisChart` in `AnalyzePage.tsx`).

Out of scope:

- The **portfolio value chart** — the backend only stores 30 days of hourly
  snapshots, so there is no older data to load.
- The **RSI/MACD sub-charts** in `AnalysisCard` — indicator sub-charts follow the
  analysis window and gain nothing from deeper history.
- The **MCP `get_candles` tool** — its surface stays unchanged.

## Approach decision

Three options were considered:

- **A. Page older bars on demand** *(chosen)* — the chart asks for one more page
  when the viewport reaches the left edge of loaded data. Only spends requests
  when the user actually zooms out, keeps a constant bar size (no seams, no
  surprise resolution change), and the page cap bounds the worst case.
- **B. Fetch the whole 10x window up front** — no viewport plumbing, but every
  chart load and every 60-second poll pulls 10x the data even for users who
  never zoom (real cost against the Twelve Data credit budget), and 10x is a
  hard ceiling.
- **C. Stitch coarser presets behind the current one** — no backend change, but
  mixes bar sizes inside one series, so there is a visible seam where 15-minute
  candles become hourly.

## Behaviour decisions

| Question | Decision |
| --- | --- |
| How deep can zooming out go? | Capped at **10x the preset window** (the base page plus 9 older pages) |
| Bar size when zoomed out | **Unchanged** — always the preset's interval |
| What the surrounding numbers reflect | **The selected preset**, not the visible window |
| Trigger | Viewport (zoom **or** pan) coming within ~10 bars of the loaded left edge |

"Numbers tied to the preset" means the trade chart's % change badge and low/high
footer keep describing the preset window — the `1D` badge stays the 1D change
however far the user zooms out — and the analyze page's strategy verdicts and
SMA/EMA/Bollinger overlays stay computed over the analysis range, so overlays
simply stop where the preset window starts. Extra bars are context only.

## Backend

### `GET /markets/{market}/candles`

One new optional query parameter:

| param | type | notes |
| --- | --- | --- |
| `end` | int, epoch **milliseconds**, exclusive, `ge=0` | omit for today's behaviour |

When `end` is absent the response is identical to today's. When present, the
endpoint returns **one page** of bars — the preset's interval and the preset's
bar count from `_RANGE_PARAMS` — covering the period immediately *before* `end`.
The `[timestamp_ms, open, high, low, close, volume]` shape is unchanged, oldest
first, and every returned bar satisfies `timestamp_ms < end`.

**Crypto** maps straight onto Bitvavo's own `end`, which is exclusive (verified:
15-minute bars with `end` at `2025-01-01T00:00:00Z` return `23:45` as the newest
bar), so `end` is forwarded unmodified.

**Stocks, funds, commodities:** `twelvedata_service.fetch_candles` gains an
`end_ms: int | None = None` argument, sent as Twelve Data's `end_date`
(`"%Y-%m-%d %H:%M:%S"`, UTC) together with the preset's `outputsize`. Twelve
Data treats `end_date` as **inclusive**, so the service drops bars with
`timestamp_ms >= end_ms` to make both sources behave identically.

`end_ms` and the existing `extra_bars` are independent and may be combined.

### Caching

The cache key becomes `f"{market}:{range_}:{end or ''}"`, so live and paged
responses never collide. Historical pages are immutable, so a page with `end`
set gets a longer TTL (`_CANDLE_HISTORY_TTL = 3600`s) than the live 60-second
entry. Because `end` values are unbounded over time, `_candle_cache` gains a
size bound: once it exceeds 500 entries, the oldest-inserted entries are dropped
until it is back under the bound (dict insertion order, no new dependency).

### Empty response

If the source has no bars before `end`, the endpoint returns `[]`. That is a
meaningful answer — the frontend reads it as "history exhausted" and stops
asking — not an error.

### Analysis endpoint

`GET /markets/{market}/analysis` is **unchanged**. The analyze chart's extra
history comes from the candles endpoint using the same range preset, so the
pages land on exactly the same bar grid as the analysis display window (both
derive interval and count from `_RANGE_PARAMS`). The oldest bar in
`analysis.candles` is the paging boundary.

## Frontend

### `frontend/src/lib/chartHistory.ts` (new)

All knowledge about extra history lives here so neither chart grows much.

```
fetchCandlePage(market, range, end?, signal?) -> Promise<Candle[]>

useOlderHistory({ market, range, baseBars })
  -> { bars, loadOlder, loading, exhausted, atCap }
```

- `bars` — the older pages followed by `baseBars` (a new array only when the
  contents actually change, so chart effects do not re-run needlessly).
- `loadOlder()` — fetches the page before the current oldest bar (`end` is
  `bars[0][0]`, exclusive, so the boundary bar is never duplicated) and prepends
  it. A no-op while a request is in flight, once `exhausted`, or once `atCap`.
- `fetchCandlePage` goes through the existing `api()` wrapper in
  `frontend/src/lib/api.ts`, so auth and timeouts behave as everywhere else.
- `exhausted` — the last page came back empty.
- `atCap` — `MAX_HISTORY_PAGES = 9` extra pages are loaded (10x the window).
- Changing `market` or `range` discards all pages and aborts any in-flight
  request.

The hook deliberately does **not** own the base bars. `PriceChart` keeps its
existing fetch and 60-second poll and passes that array in; `AnalysisChart`
passes `analysis.candles`. One source of truth per chart, identical paging for
both.

### Viewport wiring

A helper in the same module subscribes to `subscribeVisibleLogicalRangeChange`
and calls `loadOlder()` when `logicalRange.from < 10`, guarded by the hook's
in-flight check so a fast scroll cannot fire a burst of requests. One page per
trigger; the next trigger re-evaluates.

### Preserving the viewport

The data array is replaced both when a page is prepended and every 60 seconds
when the poll returns; today both paths call `fitContent()`, which is why zoom
is currently wiped every minute. The fix captures the visible range as
**timestamps** (`timeScale().getVisibleRange()`), which stay stable when bars
are prepended, and restores them with `setVisibleRange()` after the update.

To keep today's default behaviour exactly, this only applies once the user has
moved. The rule is computable, with no interaction tracking: if no extra pages
are loaded **and** the visible logical range still spans the whole dataset
(`from <= 0 && to >= bars.length - 1`), the chart calls `fitContent()` as it
does now, so a fresh chart still auto-follows the newest bar. Otherwise the
range is preserved. `getVisibleRange()` returns `null` before there is data, in
which case `fitContent()` is used.

### `PriceChart` changes

- Its `stats` memo (% change, low, high) and `chartExtent` read the **base**
  bars, so the badge and footer stay preset-scoped.
- The series data and trade markers read the **extended** `bars`, so zooming out
  reveals older trades on the chart.
- The price axis needs no change: lightweight-charts already autoscales to the
  visible bars, so a wider viewport widens the axis on its own. The footer's
  low/high may therefore sit inside the visible price axis once the user zooms
  out past the preset — intended, since the footer describes the preset window.
- Range and chart-type buttons are unchanged. Switching either resets history
  via the hook's `market`/`range` reset.

### `AnalysisChart` changes

Takes the extended `bars` for its candlestick series; overlay line series and
price lines continue to come from the analysis payload and therefore end where
the analysis window starts.

### Loading indicator and i18n

A small translated label (`chart.loadingHistory`) shows while a page is in
flight, positioned inside the chart frame; nothing is shown when history is
exhausted or the cap is reached. Added to both `frontend/src/locales/en.json`
and `nl.json`.

## Error handling

| Situation | Behaviour |
| --- | --- |
| Page request fails (network, 502 from the upstream source) | Keep the bars already loaded, drop the in-flight flag, show no error banner; the next viewport trigger retries |
| Page returns `[]` | Mark `exhausted`, stop triggering |
| Cap reached | Stop triggering; zooming further just stretches the loaded bars |
| Market or range changes mid-flight | Abort via `AbortSignal`, discard the response |
| Base fetch fails | Unchanged from today (the existing chart error state) |

## Testing

**Backend** — `backend/test_candles_paging.py`, following the existing flat
`test_*.py` pattern with upstream HTTP stubbed:

- `end` is forwarded to Bitvavo unmodified
- a page contains only bars with `timestamp_ms < end`
- omitting `end` reproduces today's response
- the Twelve Data path formats `end_date` from `end_ms` and drops the inclusive
  boundary bar
- live and paged cache keys do not collide, and paged entries use the longer TTL
- the cache stays within its size bound after many distinct `end` values

**Frontend** — a vitest test for `chartHistory.ts` against a mocked `api`:

- pages accumulate oldest-first ahead of the base bars
- `MAX_HISTORY_PAGES` stops further fetches
- an empty response sets `exhausted` and prevents further calls
- a concurrent trigger while a request is in flight issues only one request
- changing `market` or `range` resets pages and aborts the in-flight request

The chart components render to canvas, so tests cover the pure logic; the charts
themselves are verified by hand in the browser (zoom out on `1H` and `1D` for a
crypto market and for a stock, confirm the default view is unchanged on load,
confirm zoom survives the 60-second refresh).
