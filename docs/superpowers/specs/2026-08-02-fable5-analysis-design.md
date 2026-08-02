# Fable5 analysis — design spec

Date: 2026-08-02
Status: implemented

## Goal

Give every asset (crypto, stock, fund, commodity) an advanced, easy-to-understand
direction outlook: one bullish/bearish/neutral verdict with a score shown on a
five-zone gauge, a confidence level, a transparent breakdown, and a historical
track record. Presented as an independent app section branded **Fable5
analysis**, following the analysis page template
(`2026-08-02-analysis-page-template-design.md`, slug `fable5`), leaving the
existing Analyze, KimiK3 and GTP56Sol sections untouched.

## Decisions

- **Fixed-weight composite of eight signals**, no ML model and no new external
  data sources: the five base TA strategies plus three Fable5-specific
  indicators (dual-horizon momentum, stochastic oscillator, ADX trend
  strength), computed from the candle feeds already integrated (Bitvavo for
  crypto, Twelve Data for stocks/funds/commodities).
- **Fixed importance weights** (unlike KimiK3's regime-doubling): weights never
  change with market conditions, so users always see the same recipe. The ADX
  regime is shown as context only.
- **Weighted-agreement confidence**: confidence reflects how much of the
  active weight agrees with the verdict, not a head count.
- **Five-zone gauge** as the hero visual: Strong down / Leaning down / No clear
  direction / Leaning up / Strong up, derived client-side from the score
  (boundaries at -60, -20, +20, +60). The API payload stays on the shared
  outlook contract (direction/score/confidence/regime/reason/contributions).
- **Track record included**: reuses the shared daily candle store and
  analyzer-agnostic walk-forward backtest.

## Components

### Outlook engine — `backend/app/services/fable5_analysis.py`

- Reuses the five strategy signals from `analysis.analyze()` (single source of
  truth for base indicator math).
- Adds three Fable5 strategies (own implementations; no imports from other
  analyzers per the isolation contract):
  - `momentum` — rate of change over 10 and 20 bars. Both positive votes
    bullish, both negative bearish, disagreement neutral.
  - `stochastic` — slow stochastic (14, 3, 3). %K below 20 votes bullish
    (oversold), above 80 bearish (overbought); otherwise a %K/%D cross within
    3 bars votes with the cross; else neutral.
  - `trend_strength` — Wilder ADX-14 with +DI/-DI. ADX >= 25 votes strongly
    with the dominant DI, 20-25 mildly, below 20 neutral (ranging).
- `compute_outlook(strategies)`:
  - Votes: bullish +1, bearish -1, neutral 0; `none` strategies excluded.
  - Fixed weights: trend 2.0; MACD, momentum, trend_strength 1.5; RSI,
    stochastic, volatility, levels_volume 1.0. Weights are returned per
    strategy for transparency.
  - `score` = weighted vote share scaled to -100..+100; `direction` = bullish
    (>= +20), bearish (<= -20), else neutral.
  - `confidence` = high/medium/low from the **weighted** fraction of active
    strategies agreeing with the direction (>= 75% / >= 55%).
  - `regime` (trending/ranging/neutral) from the ADX value, context only.
  - i18n-ready `reason: {code, params}` with per-signal vote counts.

### Candle persistence and track record (shared primitives)

- Uses the existing `MarketCandle` store and `CandleHarvestService`
  (`services/candle_store.py`) — no new tables or harvest cadence.
- Track record via the analyzer-agnostic `backtest.track_record(candles,
  fable5_analysis.analyze_fable5)` over the latest 400 stored daily bars,
  cached 1h per market in Fable5's own `_fable5_track_record_cache`.

### API + MCP

- `GET /markets/{market}/fable5-analysis?range=...` in `routers/markets.py`:
  same candle fetch (display window + 60 warm-up bars) and 60s cache as the
  existing analysis endpoints; adds `outlook` and `track_record`.
- `GET /markets/fable5-outlooks` (declared before `/{market}/...`): outlook
  summary `{direction, score, confidence, regime}` per market from stored
  daily candles, single payload cached 15 min.
- MCP tool `get_fable5_analysis(market, range)` mirrors the REST handler,
  documented in `AGENTS.md` and covered by `mcp_smoke_test.py`.

### Frontend — `frontend/src/pages/Fable5AnalysisPage.tsx`

- Routes `/fable5-analysis` (sortable asset picker per the template) and
  `/fable5-analysis/:market`.
- Entry points: "Fable5" button next to Analyze/KimiK3/GTP56Sol on the Trade
  page, cross-link on the Analyze page, desktop nav entry and mobile More
  sheet (`nav.fable5`).
- Detail page: header (market, live price, range selector), outlook hero card
  with a **semicircular five-zone SVG gauge** (needle at the score, zone label
  underneath), confidence meter, plain-language sentence, regime note,
  expandable "Why this outlook?" listing each strategy's vote, weight and
  reason, track-record card, and the educational disclaimer. Polls every 60s.
  All strings via i18n (`fable5Analysis.*` in `en.json` / `nl.json`).
- Types: `Fable5Analysis` / `Fable5Outlooks` wrappers in `lib/types.ts`
  reusing the generic `Outlook`, `TrackRecord`, `OutlookSummary`.

## Testing

- `backend/test_fable5_analysis.py` (standalone script, same convention as
  `test_analysis.py` / `test_kimi_analysis.py`): ROC/stochastic/ADX math,
  composite scoring (unanimous, split, thresholds, fixed weights,
  weighted-agreement confidence, no-data), `analyze_fable5` integration, and
  track-record behavior on synthetic trends.
- `backend/test_analysis.py` and `backend/test_kimi_analysis.py` must pass
  unchanged (isolation smoke test); frontend `tsc -b && vite build` clean.

## Constraints and notes

- No new external data sources and no extra load on providers: Fable5 reads
  the same shared candle harvest as the other analyzers.
- The outlook is an educational indication, not a prediction; the disclaimer
  is shown on the page and documented in the MCP tool.
