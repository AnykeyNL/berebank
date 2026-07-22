# Asset Analysis Feature — Design

Date: 2026-07-22
Status: Approved (chat), implementing

## Goal

Give traders a dedicated analysis page per asset and expose the same analysis via MCP.
An "Analyze" button on the trade page (`/trade/:market`) links to `/analyze/:market`,
where the user picks a past period and sees five technical-analysis strategies, each
with an interpreted signal (bullish / bearish / neutral), the underlying values, chart
overlays, and a plain-language explanation of how the strategy works.

## Decisions (from brainstorming)

- **Strategies:** all five groups — trend/moving averages, RSI, MACD,
  volatility (Bollinger Bands + ATR), support/resistance + volume.
- **Output style:** interpreted signal per strategy with a short reason, plus chart
  overlays. No combined overall verdict.
- **Periods:** existing chart ranges plus 90 and 180 days: `1d, 1w, 30d, 90d, 180d, 365d`
  (`1h` excluded — too few bars for meaningful indicators).
- **Computation location:** backend service, shared by the REST endpoint and a new
  `analyze_market` MCP tool, so web and MCP results are identical for crypto (Bitvavo)
  and stocks/funds (Twelve Data).

## Data flow

Bitvavo REST (crypto) and Twelve Data `/time_series` (stocks/funds) supply OHLCV
candles. The analysis endpoint fetches candles for the chosen range **plus ~60 warm-up
bars** (indicators like SMA-50 need history before the displayed window), computes all
indicators over the full series, then trims returned series to the display window.
Results are cached per `market:range` for 60 seconds, like the candles endpoint.

## Strategies (v1)

| Strategy | Indicators | Signal logic (summary) |
| --- | --- | --- |
| Trend | SMA-20, SMA-50, EMA-12/26 | Bullish when fast MA above slow MA and price above slow MA; flags recent golden/death crosses |
| RSI | RSI-14 | >70 overbought (bearish warning), <30 oversold (bullish candidate), otherwise neutral with direction |
| MACD | MACD(12, 26, 9) | Signal-line crossovers and histogram direction |
| Volatility | Bollinger(20, 2σ), ATR-14 | Price at a band = stretched; band squeeze = breakout pending; ATR gives typical move per bar and a suggested stop-loss distance |
| Levels + volume | Pivot highs/lows clustered, volume vs average | Nearest support/resistance vs price; whether recent moves happen on above-average volume |

Each strategy returns `{signal, reason: {code, params}, values, series}`. The
structured reason code lets the frontend localize the sentence (en/nl); values are
decimal strings like the rest of the API. Insufficient data degrades to a per-strategy
"not enough data" state, never an error.

## Components

### Backend

1. **Extended ranges** — add `90d` and `180d` to `_RANGE_PARAMS` in
   `backend/app/routers/markets.py` (Bitvavo: `1d` × 90/180) and
   `backend/app/services/twelvedata.py` (`1day` × 63/126 trading days).
2. **`backend/app/services/analysis.py`** — pure functions over the candle shape
   `[timestamp_ms, open, high, low, close, volume]`: `sma`, `ema`, `rsi`, `macd`,
   `bollinger`, `atr`, pivot clustering, volume stats, and
   `analyze(candles, display_from_ts)` producing the full response dict.
3. **`GET /markets/{market}/analysis?range=30d`** in `markets.py` — returns
   `{market, range, generated_at, candles, strategies: {trend, rsi, macd, volatility,
   levels_volume}}`. Fetches warm-up candles via its own range map (same intervals as
   chart ranges, larger limits), reusing the Bitvavo/Twelve Data fetch paths.
4. **MCP** in `backend/app/mcp_server.py` — new `analyze_market(market, range="30d")`
   tool calling the REST handler (existing `_current_user` / `ToolError` pattern);
   each strategy includes a one-line English `explanation` for agents. Also extend
   `get_candles` with an optional `range` parameter (previously hardcoded to 1d).

### Frontend

1. **Route** `/analyze/:market` in `App.tsx`; "Analyze" button on `TradePage` next to
   the News toggle.
2. **`pages/AnalyzePage.tsx`** — header with market name, live price, back link;
   period selector (`1d…365d`, PriceChart button-group styling); main
   lightweight-charts candlestick chart with toggleable overlays (SMA/EMA lines,
   Bollinger bands, support/resistance price lines); refreshes every 60 s.
3. **`components/AnalysisCard.tsx`** — one card per strategy: name, signal badge
   (green/red/slate), localized reason sentence, key values, expandable "How this
   works" explanation; RSI and MACD cards embed a small indicator sub-chart.
4. **i18n** — new `analyze` namespace in `en.json`/`nl.json`: strategy names,
   explanations, signal labels, reason templates keyed by backend `reason.code`,
   insufficient-data message; `90d`/`180d` added under `chart.ranges`.

## Error handling

- Unknown market → 404; invalid range → 400 (mirrors candles endpoint).
- Upstream candle failure → 502 with source named (Bitvavo / Twelve Data).
- Per-strategy insufficient data → `signal: "none"`, reason code
  `insufficient_data` (page shows a muted card, MCP returns it verbatim).

## Testing

Standalone verification script `backend/test_analysis.py` (same style as
`test_stop_loss.py`): indicator math against hand-computed/known values (SMA, EMA,
RSI, MACD, Bollinger, ATR), signal classification cases, warm-up trimming, and
insufficient-data behaviour.

## Constraints

- Twelve Data candle fetches cost API credits; on-demand fetching plus the 60 s cache
  keeps usage in line with the existing candles endpoint.
- Signals are educational, not financial advice — the page carries a disclaimer
  consistent with the simulation framing.
