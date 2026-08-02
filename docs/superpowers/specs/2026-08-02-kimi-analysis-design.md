# KimiK3 analysis — design spec

Date: 2026-08-02
Status: implemented

## Goal

Give every asset (crypto, stock, fund, commodity) an advanced, easy-to-understand
direction outlook: one bullish/bearish/neutral verdict with a score, a confidence
level, a transparent breakdown, and a historical track record. Presented as an
independent app section branded **KimiK3 analysis**, leaving the existing Analyze
page (five independent strategies, no combined verdict) untouched.

## Decisions

- **Composite signal score**, no ML model and no new external data sources: the
  outlook blends the existing TA strategies, which run on the candle feeds already
  integrated (Bitvavo for crypto, Twelve Data for stocks/funds/commodities).
- **Track record included**: daily candles are persisted in a new table so the
  outlook's historical accuracy per market can be shown.

## Components

### Outlook engine — `backend/app/services/kimi_analysis.py`

- Reuses the five strategy signals from `analysis.analyze()` (single source of
  truth for indicator math).
- Adds `adx()` (Wilder-smoothed ADX/+DI/-DI, 14 bars) and a sixth strategy
  `trend_strength`: strong trend (ADX >= 25) or mild trend (ADX >= 20) votes with
  the +DI/-DI direction; ADX < 20 (ranging) votes neutral.
- `compute_outlook(strategies)`:
  - Votes: bullish +1, bearish -1, neutral 0; `none` strategies excluded.
  - Regime-aware weights: ADX >= 25 doubles trend + MACD votes; ADX < 20 doubles
    RSI + Bollinger votes. Weights are returned per strategy for transparency.
  - `score` = weighted vote share scaled to -100..+100; `direction` = bullish
    (>= +20), bearish (<= -20), else neutral.
  - `confidence` = high/medium/low from the fraction of active strategies
    agreeing with the direction (>= 80% / >= 60%).
  - i18n-ready `reason: {code, params}` with per-signal vote counts.

### Candle persistence — `backend/app/models.py` + `services/candle_store.py`

- `MarketCandle` table: market + UTC day (unique), OHLCV as `Money` decimals.
- `CandleHarvestService` (same pattern as `PortfolioSnapshotService`): catch-up
  30s after startup, then every 6h. Crypto: Bitvavo `1d` candles (400 bars per
  market, 0.1s apart). Stocks/funds/commodities: Twelve Data `time_series`
  (400 daily bars), throttled to 8s between calls and at most once per day to
  stay within the API credit budget. Rows are upserted (the still-forming current
  day refreshes); data older than 450 days is pruned.

### Track record — `backend/app/services/backtest.py`

- Walk-forward over stored daily candles: per day (after 60 warm-up days), run
  the full outlook on data up to that day and compare the direction against the
  realized 5-day forward return. Neutral outlooks make no claim and are skipped.
- Returns hit rate, sample count, average forward return per direction, and the
  evaluated period; `None` below 10 samples (UI shows a "collecting history"
  note instead).

### API + MCP

- `GET /markets/{market}/kimi-analysis?range=...` in `routers/markets.py`: same
  candle fetch (display window + 60 warm-up bars) and 60s cache as the existing
  analysis endpoint; adds `outlook` and `track_record` (cached 1h per market).
  Existing `/analysis` endpoint unchanged.
- MCP tool `get_kimi_analysis(market, range)` mirrors it, reusing the REST
  handler like the other tools.

### Frontend — `frontend/src/pages/KimiAnalysisPage.tsx`

- Routes `/kimi-analysis` (asset picker: search + asset-class filter, same
  pattern as the Trade page list) and `/kimi-analysis/:market`.
- Entry points: "KimiK3" button next to Analyze on the Trade page, cross-link on
  the Analyze page, nav entry in the desktop menu and the mobile More sheet.
- Page: header (market, live price, range selector), outlook hero card (large
  direction badge, -100..+100 score bar, 3-segment confidence meter, one
  plain-language sentence, regime note), expandable "Why this outlook?" listing
  each strategy's vote, weight and reason, track-record card, and the
  educational disclaimer. Polls every 60s. All strings via i18n
  (`kimiAnalysis.*` in `en.json` / `nl.json`).

## Testing

- `backend/test_kimi_analysis.py` (standalone script, same convention as
  `test_analysis.py`): ADX math, regime classification, composite scoring
  (unanimous, split, threshold, weighting, no-data), `analyze_kimi` integration,
  and track-record behavior on synthetic trends. 37 checks.
- `backend/test_analysis.py` still passes unchanged (47 checks); frontend
  `tsc -b && vite build` is clean.

## Constraints and notes

- No new external data sources; the only new data work is internal persistence
  of daily candles.
- The first harvest backfills ~400 daily bars per market, so the track record is
  useful immediately after deploy; Twelve Data markets need a configured API key.
- The outlook is an educational indication, not a prediction; the disclaimer is
  shown on the page and documented in the MCP tool.
