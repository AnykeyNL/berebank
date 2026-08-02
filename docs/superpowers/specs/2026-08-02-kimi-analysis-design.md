# KimiK3 analysis — design spec

Date: 2026-08-02
Status: implemented (extended the same day — see "Extension: short-term
context signals and buy/sell scores" below)

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

## Extension: short-term context signals and buy/sell scores

Evaluated against `docs/external_source.md`, the original six-signal design
left the highest-value short-term data from the paid subscriptions unused:
context (funding, open interest, Fear & Greed, VIX, yields, insiders,
earnings) could only nudge a near-neutral score by ±8–15 points and never
flip a verdict. The extension turns context into full regime-weighted votes
per asset class, adds the two classic short-term price strategies, and adds
buy/sell scores. The walk-forward track record still uses the price
strategies only (context is live-only by nature).

### Signal changes

- **Price strategies (6 → 8)**: adds `momentum` (rate of change over 10 and
  20 bars: both up bullish, both down bearish, disagreement neutral) and
  `stochastic` (slow stochastic 14/3/3: %K < 20 oversold bullish, > 80
  overbought bearish, a %K/%D cross within 3 bars votes with the cross).
  Regime-aware weighting extends: trending (ADX >= 25) doubles trend, MACD
  and momentum; ranging (ADX < 20) doubles RSI, Bollinger and stochastic
  (still suppressed near earnings or extreme funding). `trend_strength`
  keeps weight 1.0 as the regime referee.
- **Context votes replace nudges**: the old ±8–15 near-neutral nudges are
  removed entirely; context strategies vote like any other strategy at a
  fixed weight of 1.0 (no regime multiplier), so external data can now flip
  a verdict. Each strategy joins the vote only for the asset class it
  belongs to.
  - **Crypto**: `fear_greed_regime` (extremes contrarian; in the middle
    zone a ±10-point 7-day change votes with sentiment momentum),
    `crypto_liquidity` (BTC dominance change + stablecoin supply change),
    `funding_regime` (level contrarian), `oi_momentum` (4h preferred, 24h
    fallback, price-confirmed: OI up + price down means new shorts, the
    case the old nudge got backwards), `oi_fast` (1h OI change ≥ 1% with a
    matching 1h price move > 0.2% — the intraday edge; the field was
    already fetched but consumed by nothing), `funding_momentum` (4h
    funding-history trend ≥ ±0.02 percentage points over 24h with a
    confirming 24h price move: funding rising while price rises means late
    crowded longs, bearish; funding falling while price falls means
    capitulation fuel, bullish; otherwise neutral), `long_short` (taker
    long/short ratio, contrarian at extremes), `liquidations` (24h
    one-sided flush, contrarian).
  - **Stocks**: `vix_regime` (level bands plus 5-day spike/cool-down),
    `yield_curve` (spread bands, US2Y-only fallback), `relative_strength`
    (20-day return vs the sector SPDR ETF, ±2 pp), `event_risk` (within 5
    days of earnings it votes neutral on purpose — the gap-risk brake),
    `insider_flow` (90-day insider buy/sell balance, promoted from
    tie-break nudge to full vote).
  - **Funds**: `vix_regime` + `yield_curve` with asset-aware routing:
    safe-haven bases (`GLD`, `BND`, `TLT`) read elevated fear as a bid
    (risk-off flight to gold/Treasuries); `IBIT` is routed the crypto macro
    context and votes `fear_greed_regime`, `crypto_liquidity` and
    `funding_regime` on BTC derivatives.
  - **Commodities**: precious metals (XAU/XAG/XPT/XPD) get the safe-haven
    `vix_regime` and inversion-bullish `yield_curve`; energy
    (WTI/XBR/URALS) omits `yield_curve` entirely — the treasury curve is
    not predictive for oil.

### Buy / sell scores

`compute_outlook` additionally returns `buy_score` and `sell_score`
(0..100): the shares of active **regime-weighted** weight voting bullish
resp. bearish (neutral votes count in the denominator, so an undecided
market scores low on both). Unlike Fable5's fixed-weight shares, Kimi's
reflect the effective weights — a doubled trend vote in a strong trend
lifts Buy more. High values on both sides surface contested markets. The
scores ride along on the outlook contract (detail endpoint,
`/markets/kimi-outlooks` summaries, MCP tool) and render as sortable Buy /
Sell columns in the KimiK3 asset picker table (default sort: Buy,
descending) plus chips on the detail hero card.

### New data integration

- **Coinglass `/api/futures/funding-rate/history`** (verified usable on
  Hobbyist, `interval >= 4h`): 4h funding trend per coin as
  `funding_rate_change_24h`, using Binance as the reference exchange (the
  history endpoint requires naming one; it is the largest perpetuals
  market). Cached 15 min per symbol in `services/coinglass.py`. Request
  budget: three cached calls per coin per 15 min (open interest +
  pairs-markets + funding history) plus one bulk funding call — inside the
  Hobbyist 30 req/min limit for realistic usage.
- **`open_interest_change_percent_1h`**: already fetched by
  `fetch_open_interest` and merged into the crypto context but consumed by
  no engine — now drives `oi_fast`.
- `crypto_context.serialize_context` exposes the funding trend and 1h OI
  fields so the supplementary context panel can show them on all analysis
  pages.

### Plumbing

- The Kimi endpoints copy the shared context and tag it with `asset_class`
  and `base` (never mutating the cached macro dicts); `IBIT-EUR`
  additionally merges the shared crypto macro context and BTC derivatives
  so the engine can gate crypto signals for it.
- `analyze_kimi(candles, display_count, context=None)` includes only the
  eight price strategies, keeping the walk-forward track record price-only
  by design; the detail and batch endpoints pass the tagged live context.

### Testing

- `test_kimi_analysis.py` extended: momentum/stochastic math, funding
  momentum and 1h-OI cases, buy/sell shares (unanimous, split,
  regime-doubled weights, no-data), asset-class gating (crypto, stock,
  GLD, BND/TLT, IBIT, energy), insider vote, price-only without context.
- `test_analysis.py` and `test_fable5_analysis.py` must pass unchanged
  (isolation smoke test); frontend `tsc -b && vite build` clean.

### Not done (documented for later)

- RSS headline sentiment (external-sources suggestion #6): needs an NLP
  scoring step; naive keyword scoring risks degrading signal quality, so it
  stays out for now (same call as Fable5).
- Twelve Data pre/post-market bars for gap analysis and the
  `/market_movers` cross-asset scanner: medium-effort plumbing and caching
  changes; next round.
- Funding/OI history harvest for backtesting the derivative signals: they
  stay live-only and out of the track record until persisted.
