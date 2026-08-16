# de BereBank — Market Data Reference

This document describes **all market and context data** available in de BereBank. Use it when building new analysis engines, MCP tools, or external AI systems that consume BereBank data.

All instruments are quoted in **EUR** as `{TICKER}-EUR`. Prices and amounts in API/MCP responses are **decimal strings** (e.g. `"1234.56"`).

---

## Quick start for a new analysis system

1. **Pick your input data** — see [Data by asset class](#data-by-asset-class) and [Supplementary context](#supplementary-context).
2. **Choose candle source:**
   - **Live OHLCV** — `GET /markets/{market}/candles` (Bitvavo or Twelve Data). Used by Analyze, KimiK3, Fable5.
   - **Stored daily OHLCV** — SQLite `market_candles` table (harvested in background). Required for GTP56Sol and bulk outlook lists.
3. **Read supplementary context** — returned as `context` on KimiK3, Fable5, and GTP56Sol detail endpoints (not on base `analyze_market`).
4. **Wire an API route** — add `GET /markets/{market}/your-analysis` in `backend/app/routers/markets.py`, following `kimi-analysis` / `fable5-analysis`.
5. **Expose via MCP (optional)** — register a tool in `backend/app/mcp_server.py` that calls the same service function.
6. **Add a frontend page (optional)** — reuse `SupplementaryContextPanel` for context display.

Existing engines live in `backend/app/services/`:

| Engine | Service file | Signals | Uses context? |
|--------|--------------|---------|---------------|
| Analyze (5 strategies) | `analysis.py` | TA only | No |
| KimiK3 | `kimi_analysis.py` | 6 TA + regime weights | Yes — adjusts score |
| Fable5 | `fable5_analysis.py` | 8 price + asset-class context votes (fixed weights) | Yes — extra strategy votes per asset class |
| GTP56Sol | `gtp56sol_analysis.py` | 22-feature k-NN | Yes — macro features in matching |
| Opus | `opus_analysis.py` (+ `opus_features.py`, `opus_calibration.py`, `opus_store.py`) | 23 cross-sectional features, weights **learned** from walk-forward IC; output is a fee-net buy/sell ranking | Own harvested macro history (`opus_macro_series`), not the live context dicts |

---

## Market universe

| Asset class | Count | Source | Trading hours |
|-------------|-------|--------|---------------|
| **crypto** | ~430 (dynamic) | Bitvavo EUR pairs in `status=trading` | 24/7 |
| **stock** | 97 | S&P 100 (`instruments.py`) | Exchange hours |
| **fund** | 27 | US ETFs (`instruments.py`) | Exchange hours |
| **commodity** | 7 | XAU, XAG, XPT, XPD, WTI, XBR, URALS | ~24/5 (weekends closed) |

**Total:** ~560 `{TICKER}-EUR` markets when Bitvavo and Twelve Data are both active.

**Collision rule:** If a stock ticker matches a Bitvavo crypto symbol, crypto wins and the stock is omitted from the Twelve Data universe.

**Facade:** `backend/app/services/market_data.py` merges Bitvavo + Twelve Data into one registry and price cache.

---

## External data sources

| Provider | Data | API key | Service module |
|----------|------|---------|----------------|
| **Bitvavo** | Crypto live prices, intraday candles, daily harvest | No (public REST + WebSocket) | `bitvavo.py` |
| **Twelve Data** | Stocks/funds/commodities quotes, candles, press releases, VIX, treasuries, earnings, insiders | Yes — Admin → Twelve Data | `twelvedata.py` |
| **Coinglass** | Crypto funding rates (all symbols), open interest (per symbol) | Yes — Admin → Coinglass or `BEREBANK_COINGLASS_API_KEY` | `coinglass.py` |
| **Alternative.me** | Crypto Fear & Greed (365-day history live; full history since 2018 for Opus) | No | `crypto_context.py`, `opus_macro.py` |
| **CoinGecko** | BTC market-cap dominance (current snapshot) | No | `crypto_context.py` |
| **DeFiLlama** | Stablecoin total supply (daily history) | No | `crypto_context.py`, `opus_macro.py` |
| **FRED** | US 2y/10y treasury yields and VIX, daily back to 1962/1990 | No | `opus_macro.py` |
| **RSS feeds** | News matched by ticker/name regex | No (BankManager configures URLs) | `rss_aggregator.py` |

---

## Data by asset class

### Crypto

| Data type | Source | Notes |
|-----------|--------|-------|
| Live `last`, `bid`, `ask`, `open`, `volume_quote` | Bitvavo WebSocket `ticker24h` | ~1 Hz batched to frontend via `GET /ws/prices` |
| Intraday candles | Bitvavo REST `/{market}/candles` | On demand |
| Stored daily candles | Bitvavo 1d harvest → `market_candles` | 400 bars normal; up to 1825 lazy for GTP56Sol |
| Supplementary context | `crypto_context.py` + optional Coinglass | See [Crypto context fields](#crypto-context-fields) |
| News | RSS only | `has_news` when RSS match count > 0 |

### Stocks

| Data type | Source | Notes |
|-----------|--------|-------|
| Live quotes | Twelve Data `/quote` (60s poll, 40 symbols/chunk) | USD→EUR via `/exchange_rate` |
| Candles | Twelve Data `/time_series` | EUR-converted |
| Stored daily candles | Twelve Data harvest | 400 bars; deep backfill for GTP56Sol |
| Supplementary context | `td_context.py` | Full: macro + earnings + insider + sector |
| News | RSS + Twelve Data `/press_releases` | |

### Funds (ETFs)

Same as stocks for live/candles/stored/news. Supplementary context is **macro only** (no earnings, insider, or sector).

### Commodities

Same as funds for live/candles/stored. News is **RSS only** (Twelve Data press releases return 404 for commodities).

---

## Live market data fields

Each market in `GET /markets` (and `list_markets` MCP tool) includes:

```json
{
  "market": "BTC-EUR",
  "base": "BTC",
  "quote": "EUR",
  "name": "Bitcoin",
  "listing": "Bitvavo",
  "asset_class": "crypto",
  "market_open": null,
  "last": "95000.00",
  "bid": "94990.00",
  "ask": "95010.00",
  "open": "94000.00",
  "change_24h_pct": "1.06",
  "volume_quote": "12345678.00",
  "has_news": true,
  "tick_size": "1",
  "amount_decimals": 8,
  "min_order_base": "0.00009259",
  "amount_quantum": "0.00000001",
  "min_order_eur": "5",
  "next_open": null,
  "next_close": null
}
```

| Field | Crypto | Stocks/funds/commodities |
|-------|--------|--------------------------|
| `bid` / `ask` | Real spread from Bitvavo | Both set to `last` |
| `market_open` | `null` (always open) | `true` / `false` from Twelve Data |
| `open` | 24h open | Previous close |
| `tick_size` | Bitvavo `tickSize` | `null` |
| `amount_decimals` | Bitvavo `quantityDecimals` (asset `decimals` as fallback) | `null` |
| `min_order_base` | Bitvavo `minOrderInBaseAsset` | `null` |
| `next_open` / `next_close` | `null` (never closes) | ISO 8601 UTC from the exchange calendar |

`tick_size`, `amount_decimals` and `min_order_base` are the venue's own sizing rules,
loaded once at startup from Bitvavo's `/markets` and `/assets`. `amount_quantum` and
`min_order_eur` are what the engine itself
enforces and are identical on every market: amounts are worked in steps of 1e-8 and no
order may be worth less than EUR 5. All decimals are plain strings, never exponent
notation — tick sizes on meme coins reach 1e-10, where `str(Decimal)` would emit `1E-10`.

### Trading hours

`market_open` is a snapshot: it answers whether trading is possible right now, and for
stocks, funds and commodities it comes from Twelve Data, which also sees halts and
unscheduled closures. What it cannot answer is *when* the market opens or closes again,
which is what an agent needs to decide whether an order is worth placing at all.

`next_open` and `next_close` on every market, and the richer `GET /markets/hours`
(`get_market_hours` over MCP), come from `exchange_calendars` via the
`backend/app/services/market_calendar.py` facade: **XNYS** for stocks and funds, the
built-in **24/5** calendar for commodities, and nothing at all for crypto, which reports
`always_open: true` with null timestamps. Holidays, Good Friday and early closes
(Thanksgiving, Christmas Eve) are included.

```json
{
  "server_time_utc": "2026-08-15T20:00:00Z",
  "hours": [{
    "asset_class": "stock", "market": "AAPL-EUR", "calendar": "XNYS",
    "always_open": false, "is_open": false, "timezone": "America/New_York",
    "next_open": "2026-08-17T13:30:00Z", "next_close": "2026-08-17T20:00:00Z",
    "current_session_end": null
  }]
}
```

The two sources are cross-checked: when the live feed and the calendar disagree the
backend logs a throttled warning. That also catches a missing `is_market_open` field,
which would otherwise silently read as closed.

The calendar is what makes order expiry meaningful. `advance_sessions()` answers "the
close of the *n*-th session ending after now", so `expires_in_sessions=2` on a Saturday
NYSE order resolves to Tuesday's close rather than forty hours later; `sessions_between()`
answers the mirror question, how much trading time passed since a given moment. A crypto
session is simply a 24-hour day.

---

## OHLCV candles

### Candle format

All candle endpoints return arrays **oldest first**:

```json
[timestamp_ms, open, high, low, close, volume]
```

Numbers may be JSON numbers or strings depending on endpoint; treat as decimals.

### Live candles — `GET /markets/{market}/candles?range=&end=`

| Range | Bitvavo interval | Twelve Data interval |
|-------|------------------|----------------------|
| `1h` | 1m × 60 | 1min × 60 |
| `1d` | 15m × 96 | 15min × 26 |
| `1w` | 1h × 168 | 1h × 35 |
| `30d` | 4h × 180 | 1day × 22 |
| `90d` | 1d × 90 | 1day × 63 |
| `180d` | 1d × 180 | 1day × 126 |
| `365d` | 1d × 365 | 1day × 250 |

**Analysis ranges** (Analyze, KimiK3, Fable5): `1d`, `1w`, `30d`, `90d`, `180d`, `365d` — not `1h`.

**Warmup:** Analysis engines use **60 extra bars** before the display window for indicator warmup (`analysis.WARMUP_BARS`).

**Paging (`end`):** optional epoch-ms bound, **exclusive**. Returns the page of
bars just before `end` at the same interval and bar count as the range, so
charts can extend history when the user zooms out. Bitvavo takes `end`
directly; Twelve Data gets `end_date` (inclusive) and the boundary bar is
dropped. Omitting `end` is unchanged behaviour.

**Cache:** Per-market candle + analysis responses cached **60 seconds**; candle
pages with `end` set are immutable and cached **1 hour**, with the candle cache
bounded to 500 entries.

### Stored daily candles — `market_candles` table

| Column | Type | Description |
|--------|------|-------------|
| `market` | string | e.g. `BTC-EUR` |
| `day` | date (UTC) | Calendar day |
| `open`, `high`, `low`, `close`, `volume` | string decimals | Daily OHLCV |

| Constant | Value | Meaning |
|----------|-------|---------|
| `HISTORY_BARS` | 400 | Normal harvest depth (~1.5 years) |
| `RETENTION_DAYS` | 2000 | Prune older rows |
| `HARVEST_INTERVAL` | 6 hours | Background harvest cadence |
| `GTP_DEEP_HISTORY_BARS` | 1825 | Lazy GTP56Sol backfill target (~5 years) |

**Load helpers** (`candle_store.py`):

- `load_recent_daily_candles(db, market, limit=400)` — bulk outlooks, track records
- `load_completed_daily_candles(db, market)` — GTP56Sol (excludes today's incomplete UTC bar)

**Admin export/import:** `GET/POST /admin/candle-history/export|import` — JSON v1, OHLCV + optional GTP56Sol deep-backfill state. **Macro/context data is not exported.**

### Opus dataset tables

Additive tables, only read and written by the Opus engine:

| Table | Content | Written by |
|-------|---------|------------|
| `opus_macro_series` | `(series_id, day, value)` for every harvested or derived daily series: `fred:us2y`, `fred:us10y`, `fred:vix`, `crypto:fear_greed`, `crypto:stablecoin_usd`, `funding:{COIN}` | `OpusHarvestService` (every 6h, upsert) |
| `opus_calibration` | One row per `(engine_version, peer_group, horizon, regime)`: learned weight vector, score-to-return bins, IC diagnostics | Nightly walk-forward calibration (min 20h between runs) |
| `opus_recommendations` | Daily snapshot of published advice per `(day, market, horizon)`, graded with the realized forward return once the horizon passes | Harvest; pruned after 400 days |

**Admin export/import:** `GET /admin/opus-dataset/status|export`, `POST
/admin/opus-dataset/import|recalibrate` — streaming **gzip NDJSON** at constant
memory, `include_candles=true` optionally bundles `market_candles` so a fresh
install can be seeded from a development machine. The candle-history endpoints
above are unchanged.

---

## Supplementary context

Context is fetched on demand, cached in memory, and attached to KimiK3, Fable5, and GTP56Sol **detail** responses as `"context"`. There is no standalone `/context` endpoint.

**Routing:** `td_context.get_market_context(market, asset_class)` delegates crypto to `crypto_context.get_market_context`.

### Crypto context fields

`context_type: "crypto"` in full internal dict; API returns serialized subset.

| Field | Source | Used in scoring |
|-------|--------|-----------------|
| `macro_regime` | Derived | KimiK3 ±15 nudge |
| `fear_greed_index` | Alternative.me | Fable5 `vix_regime` slot; GTP56Sol `vix_normalized` |
| `fear_greed_classification` | Alternative.me | Display |
| `fear_greed_change` | Alternative.me | Fable5 mid-range sentiment momentum vote |
| `btc_dominance` | CoinGecko | Fable5 `yield_curve` slot (via `_crypto_liquidity`); GTP56Sol `yield_spread` |
| `btc_dominance_change_pct` | CoinGecko | Fable5 liquidity vote |
| `btc_correlation` | Bitvavo 30d daily returns vs BTC-EUR | KimiK3 nudge; GTP56Sol `earnings_proximity` |
| `stablecoin_supply_usd` | DeFiLlama | Display |
| `stablecoin_supply_change_pct` | DeFiLlama | KimiK3 tie-breaker; GTP56Sol `insider_activity` |
| `funding_rate_avg` | Coinglass (requires key) | Fable5 `funding_regime`; KimiK3 nudge; GTP56Sol `funding_normalized` |
| `open_interest_change_percent_24h` | Coinglass (requires key) | Fable5 `oi_momentum` (price-confirmed); KimiK3 nudge; GTP56Sol `oi_change_24h` |
| `open_interest_change_percent_4h` | Coinglass (requires key) | Fable5 `oi_momentum` preferred short window |
| `open_interest_usd` | Coinglass | Display; Fable5 liquidation calm floor |
| `long_short_ratio` | Coinglass `/pairs-markets` (requires key) | Fable5 `long_short` contrarian vote |
| `long_liquidation_usd_24h`, `short_liquidation_usd_24h` | Coinglass `/pairs-markets` (requires key) | Fable5 `liquidations` vote; display |

**Internal series (not in API, used by GTP56Sol historical features):** `fear_greed_by_day`, `stablecoin_supply_by_day`. BTC dominance history is current-only on CoinGecko free tier.

**Coinglass Hobbyist pattern:** One bulk call to `/api/futures/funding-rate/exchange-list` (all symbols) + two calls per coin (cached 15 min each): `/api/futures/open-interest/exchange-list?symbol=BTC` and `/api/futures/pairs-markets?symbol=BTC` (aggregated cross-exchange long/short volume and liquidations). `/api/futures/coins-markets` requires a higher plan.

### Non-crypto context fields (stocks / funds / commodities)

| Field | Stocks | Funds / commodities |
|-------|--------|---------------------|
| `macro_regime` | ✓ | ✓ |
| `vix_level`, `vix_change_pct` | ✓ (VIX or VIXY proxy) | ✓ |
| `us2y_yield`, `us10y_yield`, `yield_spread` | ✓ (10Y may be null on some TD plans) | ✓ |
| `days_to_earnings`, `earnings_near` | ✓ (≤5 days) | ✗ |
| `insider_signal`, `insider_buys`, `insider_sells` | ✓ (90-day) | ✗ |
| `sector_etf`, `sector_relative_return` | ✓ (20d vs sector SPDR) | ✗ |

**Fable5 mapping (non-crypto):** `vix_regime` (level + 5-day change; safe-haven inverted for precious metals) and `yield_curve` (inverted-curve bullish for precious metals; omitted for energy commodities), 1.0 weight each. Stocks additionally get `relative_strength` (20d vs sector SPDR) and `event_risk` (earnings ≤5 days votes neutral as a score brake), 1.0 weight each.

**GTP56Sol macro feature slots** (`macro_features_at` in `td_context.py`):

| Slot | Crypto source | Stock source |
|------|---------------|--------------|
| `vix_normalized` | Fear & Greed | VIX level |
| `yield_spread` | BTC dominance − 50 | 10Y−2Y spread |
| `earnings_proximity` | BTC correlation | Earnings-near flag |
| `insider_activity` | Stablecoin supply change | Insider signal |
| `funding_normalized` | Coinglass funding | — |
| `oi_change_24h` | Coinglass OI change | — |

### Bulk outlook context (list views)

| Endpoint | Crypto context | Non-crypto context |
|----------|----------------|-------------------|
| `/kimi-outlooks`, `/fable5-outlooks` | Shared crypto macro only (no per-coin Coinglass OI) | Shared TD macro only |
| `/gtp56sol-outlooks` | None | None |

Per-market detail pages include full context including Coinglass per-symbol data.

---

## News

| Endpoint | Description |
|----------|-------------|
| `GET /markets/{market}/news?limit=1-10` | Per-market articles |
| `GET /news` | Paginated global feed |

**Sources:** RSS (all asset classes) + Twelve Data press releases (stocks and funds only).

**Matching:** Regex on ticker and asset name (`rss_aggregator.py`). Cached 5 minutes.

**MCP:** `get_news(market, limit=10)`.

---

## REST API — market data endpoints

Base path: `/markets` (requires JWT except `/health`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/markets` | List all markets + live prices. Query: `asset_class`, search |
| `GET` | `/markets/technical-outlooks` | Bulk 5-strategy TA outlook (stored dailies, ≥60 bars) |
| `GET` | `/markets/kimi-outlooks` | Bulk KimiK3 outlooks |
| `GET` | `/markets/fable5-outlooks` | Bulk Fable5 outlooks |
| `GET` | `/markets/gtp56sol-outlooks?horizon=1d\|1w\|1m` | Bulk GTP56Sol (fast path) |
| `GET` | `/markets/opus-rankings?horizon=1d\|1w\|4w&asset_class=&side=&limit=` | Opus fee-net buy/sell ranking of every market + diversified basket |
| `GET` | `/markets/opus-outlooks?horizon=` | Bulk Opus outlooks |
| `GET` | `/markets/{market}/candles?range=` | Live OHLCV |
| `GET` | `/markets/{market}/analysis?range=` | Five-strategy TA (no context) |
| `GET` | `/markets/{market}/kimi-analysis?range=` | KimiK3 + `context` + `track_record` |
| `GET` | `/markets/{market}/fable5-analysis?range=` | Fable5 + `context` + `track_record` |
| `GET` | `/markets/{market}/gtp56sol-analysis?horizon=` | GTP56Sol forecast + `context` |
| `GET` | `/markets/{market}/opus-analysis?range=&horizon=` | Opus recommendation, feature table, calibration provenance, walk-forward + live track record |
| `GET` | `/markets/{market}/news?limit=` | News items |

The four per-market analysis endpoints also take `verbose` (default `true`). With
`verbose=false` the response drops the `candles` of the display window and empties every
`strategies[*].series`, keeping the signals, reasons, values, explanations and the whole
outlook block. The web app needs the chart payload and leaves the default alone; the MCP
tools default to `verbose=false`. The trim lives in `backend/app/services/payload.py` and
returns a copy, so the TTL caches keep the full response.

**Track record** (KimiK3, Fable5, Opus): Hit rate of past directional outlooks vs 5-day forward return; needs ≥70 stored daily bars. Opus adds a **live** record: the graded hit rate of the recommendations it actually published (≥20 samples).

### GTP56Sol specifics

- **Input:** Stored **completed** daily candles (not live fetch for the forecast body).
- **Horizons:** `1d` → 1 forward bar, `1w` → 5, `1m` → 21.
- **Output:** `Up` / `Sideways` / `Down` probabilities, direction, confidence, walk-forward validation, drivers.
- **Peer fallback:** Same `asset_class` only, max 8 peers, when `< MIN_SAMPLES` (30) local candidates.
- **Engine version:** `ENGINE_VERSION = "3"` — bumps forecast cache key when features change.
- **Cache:** 3600s per market + horizon + history signature.

### Opus specifics

- **Input:** Stored daily candles (260-day panel for scoring, up to 1500 days for
  calibration) plus `opus_macro_series`; the detail page additionally fetches
  live candles for its chart.
- **Peer groups:** `crypto`, `stock`, `other` (funds + commodities). Every feature
  is a rank z-score *within the peer group on that day*.
- **Horizons:** `1d` → 1 forward bar, `1w` → 5, `4w` → 21.
- **Weights:** learned per `(peer_group, horizon, regime)` from the walk-forward
  information coefficient; the payload states whether the weights in use were
  learned or are the research prior.
- **Output:** `expected_return_pct`, `net_edge_pct` (round-trip fees for the
  calling user's tier), `net_edge_limit_pct`, `conviction`, `buy_score` /
  `sell_score`, `action` (`strong_buy … sell`), `suggested_order_type`,
  `tradable_now`, plus the −100..+100 gauge score of the shared outlook contract.
- **Gates:** median euro turnover ≥ 25k, stale data (3 bars crypto / 6 others),
  market hours, low-volatility cut-off; gated rows rank last and are never
  advised as buys.
- **Engine version:** `ENGINE_VERSION = "opus-1"` — part of the calibration key.
- **Cache:** one shared scoring pass per 900s; detail responses 60s.

---

## MCP tools (AI access)

**Endpoint:** `{BEREBANK_PUBLIC_URL}/mcp` — OAuth 2.1 (see `AGENTS.md`).

### Market-data read tools

| Tool | REST equivalent | Notes |
|------|-----------------|-------|
| `list_markets` | `GET /markets` | Filter: `asset_class`, `filter` substring; carries the order sizing rules and `next_open`/`next_close` |
| `get_market_hours` | `GET /markets/hours` | Filter: `market` or `asset_class`; calendar timestamps plus the live open flag |
| `get_candles` | `GET /markets/{market}/candles` | Ranges: `1h`–`365d` |
| `analyze_market` | `GET /markets/{market}/analysis` | 5 TA strategies, no context; `verbose=false` by default |
| `get_kimi_analysis` | `GET /markets/{market}/kimi-analysis` | Includes `context`, `track_record`; `verbose=false` by default |
| `get_fable5_analysis` | `GET /markets/{market}/fable5-analysis` | Includes `context`, `track_record`; `verbose=false` by default |
| `get_gtp56sol_analysis` | `GET /markets/{market}/gtp56sol-analysis` | Horizons: `1d`, `1w`, `1m` |
| `get_opus_rankings` | `GET /markets/opus-rankings` | Ranked buy or sell list + basket; horizons `1d`, `1w`, `4w` |
| `get_opus_analysis` | `GET /markets/{market}/opus-analysis` | Feature table, calibration provenance, both track records; `verbose=false` by default |
| `get_opus_portfolio_advice` | Opus ranking joined to `GET /portfolio` | Exit opinion per holding + cash-sized buy candidates; read-only |
| `get_outlooks` | The five bulk outlook endpoints | Verdicts only, filtered to `markets` or an `asset_class`; engines `technical`, `kimi`, `fable5`, `opus`, `gtp56sol` |
| `get_news` | `GET /markets/{market}/news` | Limit 1–10 |

`get_outlooks` is a projection of the bulk endpoints, not a new engine: it calls the same
handlers and therefore hits the same 900s caches, costing no extra provider requests. Note
that the bulk endpoints run on stored daily candles over the full history, while a
per-market tool scores the range you ask for (4h bars for crypto on `30d`, for instance),
so the two can differ slightly. Opus horizons (`1d`/`1w`/`4w`) are the tool's vocabulary;
`4w` maps to GTP56Sol's `1m`.

### Not exposed via MCP

Admin candle import/export, RSS admin, raw supplementary context without analysis.

### Account / trading tools

`get_account_status`, `get_portfolio`, `get_portfolio_history`, `list_orders`, `list_trades`, `get_trade_history`, `get_leaderboard`, `get_leaderboard_history`, `place_order`, `cancel_order` — see `AGENTS.md`.

---

## Configuration

### Admin settings (`PUT /admin/settings`)

| Key | Purpose |
|-----|---------|
| `twelvedata_api_key` | Required for non-crypto live data, candles, press releases, macro |
| `coinglass_api_key` | Crypto funding + open interest |
| `bitvavo_api_key` / `bitvavo_api_secret` | Stored; not used by current market-data code |

### Environment variables

| Variable | Purpose |
|----------|---------|
| `BEREBANK_COINGLASS_API_KEY` | Fallback if DB Coinglass key unset |
| `BEREBANK_PUBLIC_URL` | OAuth issuer, MCP base URL |
| `BEREBANK_DATABASE_URL` | SQLite/Postgres (stores `market_candles`) |

---

## Caching and rate limits

### HTTP response caches

| Data | TTL |
|------|-----|
| Candles + Analyze + Kimi + Fable5 + Opus detail | 60s |
| GTP56Sol detail | 3600s |
| Bulk outlook lists | 900s |
| Opus scoring pass (shared by rankings, outlooks, detail) | 900s |
| Track records | 3600s |
| News | 300s |

### In-memory context caches

| Cache | TTL |
|-------|-----|
| Crypto macro (FNG, dominance, stablecoins) | 900s |
| Crypto per-market (correlation + Coinglass OI) | 3600s |
| Coinglass funding map (all symbols) | 900s |
| Coinglass OI per symbol | 900s |
| TD macro (VIX, treasuries) | 900s |
| TD per-market (earnings, insider, sector) | 3600s |

### Provider pacing

| Provider | Behavior |
|----------|----------|
| Bitvavo harvest | 0.1s between ~430 markets |
| Twelve Data quotes | 60s poll, 40 symbols/request |
| Twelve Data harvest | 8s delay, max once per 20h |
| Coinglass | 429 → log warning, use stale/empty |
| RSS | Poll every 3600s |

---

## Adding a new analysis engine — checklist

### 1. Define inputs

```python
# Typical signature (live-candle engines):
def analyze_yours(candles: list[list], display_count: int, context: dict | None = None) -> dict:
    ...
```

- **`candles`:** Oldest-first OHLCV arrays from Bitvavo or Twelve Data.
- **`context`:** From `get_market_context()` — may be `None` if provider keys missing.
- **`display_count`:** Trailing bars shown to the user; earlier bars are warmup only.

### 2. Reuse base indicators

Import shared math from `analysis.py` (SMA, EMA, RSI, MACD, Bollinger, ATR, support/resistance). KimiK3 and Fable5 follow this pattern.

### 3. Consume supplementary context

```python
from .td_context import get_market_context, serialize_context

td_context = await get_market_context(market, market_info["asset_class"])
result = your_service.analyze(candles, display_count, td_context)
return {..., "context": serialize_context(td_context)}
```

For crypto-specific fields, read `context.get("context_type") == "crypto"` and document which fields your engine uses.

### 4. Add route + MCP tool

- Route: `backend/app/routers/markets.py` — mirror `kimi-analysis` caching pattern.
- MCP: `backend/app/mcp_server.py` — thin wrapper calling the same service.

### 5. Track record (optional)

Use `backtest.py` with stored daily candles — same 5-day forward window as KimiK3/Fable5.

### 6. GTP56Sol-style engine (optional)

If you need **historical pattern matching:**

- Read from `load_completed_daily_candles`, not live candles.
- Add features to a named tuple/list; bump `ENGINE_VERSION` when features change.
- Map context via `macro_features_at(context, timestamp_ms, current_only=...)`.
- Register lazy backfill in `candle_store.ensure_gtp56sol_deep_history` if you need >400 bars.

### 7. Cross-sectional engine (optional)

If your engine ranks markets against each other instead of scoring one in
isolation, follow Opus:

- Build the panel once per pass (`opus_store.compute_scores`) and cache it; per-market
  requests then read from that pass instead of recomputing.
- Rank features within a peer group per day; a day-constant macro reading says
  nothing about which market to hold, so regress each market against it instead.
- Learn weights from the walk-forward information coefficient and state in the
  payload whether they were learned or are a prior.
- Express the verdict after fees (`fees.get_30d_volume` + `fees.get_fee_rates`), and let the engine say
  "no trade" when nothing clears the round trip.

### 8. Tests

Add `backend/test_your_analysis.py` following `test_kimi_analysis.py` / `test_fable5_analysis.py` / `test_opus_analysis.py` patterns.

---

## Base technical analysis (shared)

`analyze_market` / `analysis.analyze` computes five strategies from OHLCV:

| Strategy | Indicators |
|----------|------------|
| `trend` | SMA 20/50 crossovers |
| `rsi` | RSI-14 |
| `macd` | MACD histogram |
| `volatility` | Bollinger Bands + ATR stop suggestion |
| `levels_volume` | Pivot support/resistance + volume ratio |

Each returns `signal` (`bullish` | `bearish` | `neutral` | `none`), `reason`, `explanation`, and optional `series` for charting.

---

## Health and status

`GET /health` returns Bitvavo connection, Twelve Data status, RSS aggregator status.

Admin UI shows Twelve Data and Coinglass configuration under **Admin → API connections**.

---

## Related documents

- [AGENTS.md](../AGENTS.md) — MCP tools and competition rules for AI agents
- [README.md](../README.md) — Development setup
- [MCP server design spec](superpowers/specs/2026-07-19-mcp-server-design.md) — OAuth and tool architecture
- Engine design specs: `docs/superpowers/specs/2026-08-02-*-analysis-design.md`, [Opus](superpowers/specs/2026-08-03-opus-analysis-design.md)
