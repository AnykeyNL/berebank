# Analysis page template — reusable design spec

Date: 2026-08-02
Status: active (governs all analysis sections; KimiK3 is the reference implementation)

## Goal

de BereBank can host multiple **analysis pages** — independent sections that each
give every market a direction outlook in their own way (KimiK3 is the first;
future ones may be other models or methodologies). This spec defines the
template so that every analysis page:

1. **Looks and behaves identically** — same picker, same detail layout, same
   vocabulary (direction, score, confidence, track record).
2. **Stays fully isolated** — each analyzer is developed, changed, or removed
   without touching another analyzer's code. Shared code is limited to a small,
   stable set of generic primitives.

Reference implementation: KimiK3 (see `2026-08-02-kimi-analysis-design.md`).

## Definitions

- **Analyzer** — one analysis methodology with its own branding (e.g. KimiK3).
- **`<slug>`** — the analyzer's lowercase identifier, used consistently
  everywhere: `kimi` for KimiK3. All artifacts below are named from the slug.

## Isolation contract

Every analyzer owns the following artifacts. None of them may be shared with or
imported by another analyzer:

| Layer | Artifact (per analyzer) |
| --- | --- |
| Backend service | `backend/app/services/<slug>_analysis.py` |
| REST endpoints | `GET /markets/{market}/<slug>-analysis` and `GET /markets/<slug>-outlooks` (in `routers/markets.py`) |
| Endpoint caches | module-level `_<slug>_cache`, `_<slug>_outlooks_cache`, `_<slug>_track_record_cache` with their own TTL constants |
| MCP tool | `get_<slug>_analysis` in `mcp_server.py` |
| Frontend page | `frontend/src/pages/<Slug>AnalysisPage.tsx` (all page-specific components live in this file) |
| Routes | `/<slug>-analysis` and `/<slug>-analysis/:market` in `App.tsx` |
| i18n namespace | `<slug>Analysis.*` in `en.json` / `nl.json`, plus `nav.<slug>` |
| Tests | `backend/test_<slug>_analysis.py` (standalone script) |

**Rules:**

- An analyzer's service module contains pure functions over candle lists. It
  never imports another analyzer's module, never touches the database, and
  never reads request state.
- Analyzer endpoints never call each other. Each computes from candles
  independently, even if that means duplicate work.
- Page-specific React components (`ScoreBar`, `ConfidenceMeter`,
  `ConfidenceDots`, `AssetPicker`, sort logic) are **deliberately duplicated**
  in each page file, not extracted. This lets pages diverge on demand.
  Only when a third analyzer ships may the identical parts be extracted into a
  shared `AnalysisPageShell` ("rule of three") — and only if they are still
  identical.

### Shared, stable primitives (allowed dependencies)

These are generic and analyzer-agnostic; using them does not break isolation:

- `services/analysis.py` — indicator math and the five base strategies.
- `services/candle_store.py` — stored daily candles (`load_daily_candles`) and
  the harvest service. One candle store serves all analyzers; never add
  per-analyzer candle tables.
- `services/backtest.py` — walk-forward track record (see "Track record"
  below for the required generalization).
- Frontend: `SignalBadge` (`components/AnalysisCard.tsx`), `AssetClassIcon`,
  `lib/api`, `lib/usePrices`, `lib/format` (`fmtPrice`, `fmtPct`,
  `fmtDateTime`), `formatReasonParams` (`pages/AnalyzePage.tsx`).
- Frontend types: `Outlook`, `OutlookConfidence`, `MarketRegime`,
  `OutlookContribution`, `TrackRecord`, `OutlookSummary` are **generic** and
  shared. Only the wrapper types are per-analyzer (`KimiAnalysis`,
  `KimiOutlooks` → `<Slug>Analysis`, `<Slug>Outlooks`).
- i18n: reuse `analyze.signals.*`, `analyze.reasons.*`, `trade.*`, `chart.ranges.*`.

## Data contract

Every analyzer speaks the same shapes so the UI vocabulary stays consistent.

### Outlook (per-market detail + batch summary)

```
direction    bullish | bearish | neutral | none
score        integer -100..+100 (signed strength of the verdict)
confidence   high | medium | low
regime       trending | ranging | neutral   (market context, may be 'neutral')
reason       { code, params }               (i18n-ready, never a raw string)
contributions  [{ strategy, signal, weight }]   (detail endpoint only)
```

### Track record (detail endpoint, nullable)

```
hit_rate_pct, samples, forward_days,
avg_bullish_return_pct, avg_bearish_return_pct, from, to
```

`null` when history is insufficient — the UI shows a "collecting history"
note. Batch endpoint omits markets without enough data instead.

## Backend blueprint

### Service module — `services/<slug>_analysis.py`

- Entry point `analyze_<slug>(candles: list[list], display_count: int) -> dict`
  returning the same top-level shape as `analysis.analyze()` plus an
  `outlook` key: `{ candles, strategies, outlook, generated_at, ... }`.
- May call `analysis.analyze()` for the base strategies, then add its own
  indicators/strategies and compute the outlook. All math is deterministic
  and unit-testable without the app.

### Endpoints — `routers/markets.py`

Route order matters: the batch route must be declared before `/{market}/...`.

- `GET /markets/{market}/<slug>-analysis?range=1d|1w|30d|90d|180d|365d`
  - Same candle fetch as the existing analysis endpoint (display window +
    60 warm-up bars), same 60s in-memory cache keyed by market+range.
  - Response: `analyze_<slug>()` result plus `track_record`.
- `GET /markets/<slug>-outlooks`
  - Iterates all markets, loads stored daily candles via
    `candle_store.load_daily_candles(db, market)`, computes only the outlook
    summary `{ direction, score, confidence, regime }` per market.
  - Single cached payload, TTL 15 min (`_<SLUG>_OUTLOOKS_TTL = 900`) — daily
    candles change slowly.
- Track record helper `_<slug>_track_record(db, market)`: cached 1h per
  market (`_TRACK_RECORD_TTL = 3600`).

### Track record — `services/backtest.py`

`backtest.track_record(candles, analyze_fn)` is analyzer-agnostic: each
analyzer's router helper passes its own entry point, e.g.
`track_record(load_daily_candles(db, market), <slug>_analysis.analyze_<slug>)`,
and caches the result in its own `_<slug>_track_record_cache` (never a shared
cache — track records differ per analyzer). Keep `FORWARD_DAYS`,
`MIN_SAMPLES`, `WARMUP_DAYS` shared unless an analyzer documents a reason to
differ.

### MCP — `mcp_server.py`

Add `get_<slug>_analysis(market, range="30d")` that wraps the REST handler
(imports it as `_get_<slug>_analysis`), mirroring `get_kimi_analysis`. Tool
names are unique per analyzer and each tool carries its own docstring
describing the methodology and the educational disclaimer, so MCP clients
always see distinct, independently documented tools; there is no shared MCP
state or registry between analyzers. Document the tool in `AGENTS.md`
(read-tools table) with one line on what makes this analyzer distinct.

## Frontend blueprint

### Routes and entry points

- `App.tsx`: `/<slug>-analysis` (picker) and `/<slug>-analysis/:market` (detail),
  both rendering `<Slug>AnalysisPage`.
- `Layout.tsx`: desktop nav link (`nav.<slug>`).
- `MobileTabBar.tsx`: add the route to `MORE_ROUTES` and a link in the More sheet.
- `TradePage.tsx`: analyzer button next to Analyze, navigating to
  `/<slug>-analysis/{selected}`.
- `AnalyzePage.tsx`: cross-link in the header; the analysis page links back to
  `/analyze/{market}` ("classic" link).

### Page anatomy — `<Slug>AnalysisPage.tsx`

One default-exported component that renders the picker when no `:market`
param is present, the detail view otherwise. All helper components are
file-local.

**Picker view** (`AssetPicker`):

- Card: page title, search input, asset-class icon filter (all / crypto /
  stock / fund / commodity).
- Fetches `GET /markets` and `GET /markets/<slug>-outlooks` once on mount;
  live prices via `usePrices()`.
- Sortable table, five columns, identical widths in header and rows:
  Asset (flex-1) · Confidence dots (w-12, hidden below `sm`) · Score
  (w-12/w-14, right, colored by sign) · Outlook badge (w-20/w-24, centered) ·
  Last price (w-16/w-20, right, mono).
- Sorting contract: click header to sort, click again to toggle asc/desc;
  new column defaults to ascending for Asset, descending for the rest;
  default sort on load is **score descending**; rows without an outlook or
  price always sort last; direction ranks bullish > neutral > bearish > none,
  confidence ranks high > medium > low.
- Rows without an outlook show the localized "collecting history" note in the
  outlook column.

**Detail view** (per market):

1. Header card: back-to-trade link, change-asset link, classic-analysis
   cross-link; title `<pageTitle>: {market}` with asset icon and name; live
   last price and range change %; range selector (`1d`…`365d`).
2. Outlook hero card: large direction badge, one plain-language sentence
   (`outlook.reason` via i18n), regime note; `ScoreBar` (−100…+100 gradient
   with marker) and `ConfidenceMeter` (3 segments); expandable "Why this
   outlook?" listing each contribution (strategy name, `SignalBadge`, weight
   chip when > 1, reason); "updated at" timestamp.
3. Track-record card: hit-rate summary, sample count and period, avg
   forward-return chips per direction; or the "collecting history" note.
4. Disclaimer line (educational simulation, not advice).

Behavior: poll the detail endpoint every 60s; skeleton pulse while loading;
error card on failure. All strings via `<slug>Analysis.*` i18n keys.

### Required i18n keys (`<slug>Analysis.*`)

`pageTitle`, `button`, `pickAsset`, `backToTrade`, `changeAsset`,
`classicLink`, `range`, `loadError`, `disclaimer`, `why`, `hideWhy`,
`updated`, `weight`, `table.{asset,outlook,collecting}`,
`outlook.{title,scoreLabel,scoreMin,scoreMax,confidenceLabel,confidenceLevels.{low,medium,high},reasons.*,regimeNote.{trending,ranging,neutral}}`,
`strategyNames.*`, `reasons.*` (for analyzer-specific strategies),
`trackRecord.{title,summary,samples,avgBullish,avgBearish,noHistory}`.
Plus `nav.<slug>`. Both `en.json` and `nl.json`.

## Testing

- `backend/test_<slug>_analysis.py`: standalone script (same convention as
  `test_analysis.py` / `test_kimi_analysis.py`) covering the analyzer's own
  indicator math, outlook scoring edge cases (unanimous, split, thresholds,
  no-data), `analyze_<slug>` integration, and track-record behavior on
  synthetic trends.
- Existing suites (`test_analysis.py`, other analyzers' tests) must pass
  unchanged — that is the isolation smoke test.
- Frontend: `npm run build` (tsc + vite) must be clean.

## Recipe: adding a new analyzer

1. Pick the `<slug>` and branding; add `nav.<slug>` and the `<slug>Analysis.*`
   i18n namespace (en + nl).
2. Write `services/<slug>_analysis.py` with `analyze_<slug>()`; add its tests.
3. Add the two endpoints + three caches to `routers/markets.py` (batch route
   before `/{market}/...`); the track-record helper calls
   `backtest.track_record(candles, analyze_<slug>)` with its own
   `_<slug>_track_record_cache`.
4. Add the MCP tool and the `AGENTS.md` line.
5. Add `<Slug>Analysis` / `<Slug>Outlooks` types in `lib/types.ts` (reuse the
   generic `Outlook` / `TrackRecord` / `OutlookSummary`).
6. Copy `KimiAnalysisPage.tsx` to `<Slug>AnalysisPage.tsx`; rename the slug,
   endpoints, and i18n namespace; adjust hero/verdict visuals only if the
   methodology needs it.
7. Wire routes, desktop nav, mobile More sheet (`MORE_ROUTES` + link), Trade
   page button, Analyze page cross-link.
8. Run backend tests + frontend build; verify isolation by running the other
   analyzers' tests untouched.

## Constraints and notes

- No analyzer may add load to external data providers beyond the shared
  candle harvest; new data needs require extending `candle_store.py` once,
  for everyone.
- The outlook is an educational indication, not a prediction; every analysis
  page shows the disclaimer and every MCP tool documents it.
- If a genuinely better shared abstraction emerges (third analyzer onward),
  refactor shared pieces across **all** analyzers in the same change — never
  leave two copies of a "shared" component drifting apart.
