# de BereBank — External Data Sources

This document describes **every external data provider** BereBank can use: what each source offers, your subscription tier where relevant, what BereBank consumes today, and what is available for future analysis engines.

For how this data flows through APIs, engines, and caching, see [marketdata.md](marketdata.md).

---

## Summary

| Provider | Cost | API key | Asset classes | BereBank status |
|----------|------|---------|---------------|-----------------|
| **Bitvavo** | Free | No | Crypto | **Fully integrated** — prices, candles, harvest |
| **Twelve Data Pro** | Paid (your plan) | Admin → Twelve Data | Stocks, funds, commodities | **Integrated** — quotes, candles, news, macro, earnings, insiders |
| **Coinglass Hobbyist** | ~$29/mo | Admin → Coinglass or `BEREBANK_COINGLASS_API_KEY` | Crypto derivatives | **Partially integrated** — funding + OI |
| **Alternative.me** | Free | No | Crypto macro | **Integrated** — Fear & Greed (live 365d; full history harvested for Opus) |
| **CoinGecko** | Free | No | Crypto macro | **Integrated** — BTC dominance (current) |
| **DeFiLlama** | Free | No | Crypto macro | **Integrated** — stablecoin supply (history harvested for Opus) |
| **FRED (St. Louis Fed)** | Free | No | Macro (yields, VIX) | **Integrated** — daily history harvested for Opus |
| **RSS feeds** | Free | No (URLs configured in Admin) | All (matched to tickers) | **Integrated** — news |

---

## Bitvavo (free, no API key)

**Website:** [bitvavo.com](https://bitvavo.com)  
**Docs:** [Bitvavo WebSocket API](https://docs.bitvavo.com/docs/websocket-api/)  
**Code:** `backend/app/services/bitvavo.py`

Public market data requires **no authentication**. BereBank does not use Bitvavo API keys for pricing (keys can be stored in Admin for future features only).

### Base URLs

| Protocol | URL |
|----------|-----|
| REST | `https://api.bitvavo.com/v2` |
| WebSocket | `wss://ws.bitvavo.com/v2/` |

### Data available

| Data | REST / WS | BereBank use |
|------|-----------|--------------|
| Market catalog | `GET /markets` | All EUR pairs in `status=trading` (~430) |
| Live ticker | WebSocket channel `ticker24h` | `last`, `bid`, `ask`, `open`, `volume` (24h quote volume) |
| OHLCV candles | `GET /{market}/candles?interval=&limit=` | Charts, analysis engines, BTC correlation |
| Intervals | `1m`, `5m`, `15m`, `1h`, `4h`, `1d`, … | Mapped from chart `range` in `markets.py` |

### Candle intervals used by BereBank

| Chart range | Bitvavo interval | Bars (approx.) |
|-------------|------------------|----------------|
| `1h` | 1m | 60 |
| `1d` | 15m | 96 |
| `1w` | 1h | 168 |
| `30d` | 4h | 180 |
| `90d` / `180d` / `365d` | 1d | 90 / 180 / 365 |

### Stored history (background harvest)

- **Source:** Bitvavo `1d` candles  
- **Depth:** 400 bars normal; up to **1825 bars** lazy backfill for GTP56Sol  
- **Table:** `market_candles`  
- **Pacing:** 0.1s between markets during harvest  

### Limits

- No formal API key rate limit on public endpoints under normal use  
- BereBank polls WebSocket continuously; REST used for candles and harvest  

---

## Twelve Data Pro (paid subscription)

**Website:** [twelvedata.com](https://twelvedata.com)  
**API base:** `https://api.twelvedata.com`  
**Code:** `backend/app/services/twelvedata.py`, `td_context.py`  
**Configuration:** BankManager → Admin → **Twelve Data connection**, or DB key `twelvedata_api_key`

Your **Pro** plan includes (per Twelve Data pricing):

- US **real-time** stocks; EOD global equities and ETFs  
- **Commodities** and **fixed income** (bonds / treasuries)  
- **Mutual funds** NAV, **pre/post-market** data, **market movers**  
- **Fundamentals** and **100+ technical indicators**  
- **WebSocket** streaming (500+ credits)  
- **70+ markets** coverage  

**Ultra-only** (not on Pro): analyst ratings, price targets, recommendations, institutional holders, full fundamental history depth, detailed ETF/mutual-fund analytics.

### BereBank instrument universe (131 curated)

Defined in `backend/app/services/instruments.py`:

| Class | Count | Examples |
|-------|-------|----------|
| **stock** | 97 | S&P 100 (`AAPL`, `MSFT`, …) |
| **fund** | 27 | `SPY`, `QQQ`, `XLK`, `GLD`, `IBIT`, … |
| **commodity** | 7 | `XAU`, `XAG`, `XPT`, `XPD`, `WTI`, `XBR`, `URALS` |

USD-denominated symbols are converted to **EUR** using live `USD/EUR` from `/exchange_rate`.

### Endpoints BereBank uses today

| Endpoint | Purpose | Output used |
|----------|---------|-------------|
| `GET /exchange_rate?symbol=USD/EUR` | FX | Converts USD quotes to EUR |
| `GET /quote?symbol=…` (batched) | Live prices | `last`, `open`, `volume`, `exchange`, market hours — polled every **60s**, 40 symbols/chunk (~130 credits/min) |
| `GET /time_series` | Candles + macro series | OHLCV arrays; VIX/VIXY/US2Y/US10Y daily closes |
| `GET /press_releases?symbol=…` | Company news | Title, body, datetime — **stocks and funds only** (commodities 404) |
| `GET /earnings_calendar?start_date=&end_date=` | Earnings schedule | Next report date per S&P 100 stock |
| `GET /insider_transactions?symbol=…` | Insider trades | Buy/sell counts for 90-day signal — **stocks only** |
| `GET /bonds` | Bond catalog | Discover `US2Y`, `US10Y`, etc. (plan-dependent list) |

### Supplementary context from Twelve Data (non-crypto)

Fetched via `td_context.py`, cached **15 min** (macro) / **1 h** (per stock).

| Field | TD source | Stocks | Funds / commodities |
|-------|-----------|--------|---------------------|
| `vix_level` | `/time_series` on `VIX` or **`VIXY`** fallback | ✓ | ✓ |
| `us2y_yield` | `/time_series` on **`US2Y`** | ✓ | ✓ |
| `us10y_yield` | `/time_series` on `US10Y` / `US30Y` | ✓ (often **null** if not in bond catalog) | ✓ |
| `yield_spread` | 10Y − 2Y | ✓ when both yields exist | ✓ |
| `macro_regime` | Derived from VIX + spread | ✓ | ✓ |
| `days_to_earnings`, `earnings_near` | `/earnings_calendar` | ✓ | ✗ |
| `insider_signal`, buys/sells | `/insider_transactions` | ✓ | ✗ |
| `sector_etf`, `sector_relative_return` | `/time_series` vs sector SPDR ETF | ✓ | ✗ |

**Plan note:** On many Pro accounts, **`VIX`** and **`US10Y`** return 404 on `/time_series`; BereBank falls back to **VIXY** (VIX proxy ETF) and uses **US2Y-only** yield logic when 10Y is missing.

### Candle intervals (Twelve Data)

| Chart range | TD interval | Bars (approx.) |
|-------------|-------------|----------------|
| `1h` | 1min | 60 |
| `1d` | 15min | 26 |
| `1w` | 1h | 35 |
| `30d` | 1day | 22 |
| `90d` | 1day | 63 |
| `180d` | 1day | 126 |
| `365d` | 1day | 250 |

GTP56Sol deep backfill requests up to **~2000 daily bars** per non-crypto market.

### On Pro but not wired in BereBank yet

These are included in your subscription and useful for new analysis engines:

| TD feature | Typical endpoint | Idea for new engines |
|------------|------------------|----------------------|
| Pre/post-market bars | `/time_series` with extended hours | Gap risk, earnings reactions |
| Market movers | `/market_movers` | Cross-asset momentum scanner |
| WebSocket streaming | WS API | Lower latency than 60s REST poll |
| Fundamentals | `/statistics`, `/profile`, `/earnings` | Valuation context (P/E, margins) |
| Technical indicators | `/rsi`, `/macd`, … (100+) | Alternative to in-house TA |
| Mutual fund NAV | Fund endpoints | NAV vs market price |
| More bond yields | `/bonds` catalog | Full yield curve if US10Y added to catalog |

### Harvest pacing

- Twelve Data daily harvest: **8 s** delay between markets, at most **once per 20 h**  
- GTP56Sol deep-history fetches: **7.5 s** min gap between TD calls  

---

## Coinglass Hobbyist (~$29/mo)

**Website:** [coinglass.com](https://coinglass.com)  
**API base:** `https://open-api-v4.coinglass.com`  
**Auth header:** `CG-API-KEY: <your key>`  
**Code:** `backend/app/services/coinglass.py`, merged in `crypto_context.py`  
**Configuration:** Admin → **Coinglass connection**, or env `BEREBANK_COINGLASS_API_KEY`

### Hobbyist plan limits

| Limit | Value |
|-------|-------|
| Rate limit | **30 requests / minute** |
| Endpoints | **80+** data endpoints |
| Update frequency | ≤ 1 minute (varies by endpoint) |
| Historical interval (funding/OI OHLC) | **`>= 4h`** only on Hobbyist |
| Commercial use | Personal / hobby projects (check ToS for hosted competitions) |

**Startup ($79/mo)** unlocks: 80 req/min, 30m+ historical intervals, `/api/futures/coins-markets` (all-coin snapshot in one call).

### Endpoints BereBank uses today

| Endpoint | Calls | Data returned | Cache |
|----------|-------|---------------|-------|
| `GET /api/futures/funding-rate/exchange-list` | **1** (all ~1650 symbols) | Per symbol: `stablecoin_margin_list`, `token_margin_list` with `funding_rate` per exchange | 15 min |
| `GET /api/futures/open-interest/exchange-list?symbol=BTC` | **1 per coin** (on demand) | Aggregate row (`exchange=All`): `open_interest_usd`, `open_interest_change_percent_1h/4h/24h`, … | 15 min per symbol |

BereBank computes **`funding_rate_avg`** = mean funding across exchanges in the exchange-list row.

**Symbol mapping:** Bitvavo `PEPE` → Coinglass `1000PEPE` when needed (`resolve_coinglass_symbol`).

### Context fields from Coinglass

| Field | Used by |
|-------|---------|
| `funding_rate_avg` | Fable5 `funding_regime`; KimiK3 score nudge; GTP56Sol `funding_normalized` |
| `open_interest_usd` | Context panel (display) |
| `open_interest_change_percent_24h` | Fable5 `oi_momentum`; KimiK3 nudge; GTP56Sol `oi_change_24h` |
| `open_interest_change_percent_1h/4h` | Fetched; available for future use |

### Endpoints available on Hobbyist but not integrated

Verified usable on Hobbyist (except where noted):

| Endpoint | Data | Notes |
|----------|------|-------|
| `/api/futures/supported-coins` | List of futures symbols | Discovery |
| `/api/futures/pairs-markets?symbol=BTC` | Per-exchange funding, OI, liquidations, long/short volume for **one coin** (~90 pairs) | Richer than exchange-list average |
| `/api/futures/funding-rate/history` | Funding OHLC | `interval >= 4h`; needs `exchange` + `symbol` |
| `/api/futures/open-interest/history` | OI OHLC | `interval >= 4h` |
| `/api/futures/open-interest/aggregated-history?symbol=BTC` | Aggregated OI history | GTP56Sol backfill candidate |
| `/api/futures/coins-markets` | All coins: funding, OI, price change in **one call** | **Upgrade plan** (401 on Hobbyist) |
| `/api/futures/coins-price-change` | Multi-timeframe price change | **Upgrade plan** on Hobbyist |

### Rate budget (Hobbyist)

With BereBank’s caching:

| Pattern | Requests / 15 min | Within 30/min? |
|---------|-------------------|----------------|
| Bulk funding refresh | 1 | ✓ |
| 10 unique alt detail pages (OI) | 1 + 10 = 11 | ✓ |
| 30 users open 30 different alts cold | ~31 in first minute | ⚠️ May hit 429; cache prevents repeat |

---

## Alternative.me — Crypto Fear & Greed (free)

**URL:** `https://api.alternative.me/fng/`  
**Code:** `crypto_context.py` → `_fetch_fear_greed`; `opus_macro.py` → `fetch_fear_greed_history`  
**API key:** None

### Request

```
GET https://api.alternative.me/fng/?limit=365&format=json    # live context
GET https://api.alternative.me/fng/?limit=0&format=json      # full history (Opus)
```

`limit=0` returns the **complete history since 2018-02-01** (~3100 days). The
live context path keeps its 365-day window; the Opus harvest stores the full
series as `crypto:fear_greed` in `opus_macro_series` so sentiment sensitivity can
be *calibrated* and not only displayed.

### Response fields used

| Field | Description |
|-------|-------------|
| `value` | Index 0–100 |
| `value_classification` | e.g. "Fear", "Greed", "Extreme fear" |
| `timestamp` | Unix seconds |

BereBank builds:

- **`fear_greed_index`** — latest value  
- **`fear_greed_classification`** — label  
- **`fear_greed_change`** — vs 7 days ago  
- **`fear_greed_by_day`** — 365-day history for GTP56Sol  

### Limits

- Free, no auth  
- BereBank cache: **15 min** (shared crypto macro)  

---

## CoinGecko — global crypto market (free)

**URL:** `https://api.coingecko.com/api/v3/global`  
**Code:** `crypto_context.py` → `_fetch_btc_dominance`  
**API key:** None (public tier)

### Response fields used

| Field | Path | Description |
|-------|------|-------------|
| BTC dominance | `data.market_cap_percentage.btc` | Bitcoin share of total crypto market cap (%) |

BereBank exposes:

- **`btc_dominance`** — current value  
- **`btc_dominance_change_pct`** — day-over-day (limited; only current snapshot stored in `btc_dominance_by_day`)  

### Limits

- Free tier: rate limits apply (~10–30 calls/min); BereBank calls **once per 15 min**  
- **No reliable free historical dominance series** — GTP56Sol uses dominance on current bar only  

### Available but not integrated

CoinGecko Pro adds historical global data, more calls — not required for current BereBank features.

---

## DeFiLlama — stablecoin supply (free)

**URL:** `https://stablecoins.llama.fi/stablecoincharts/all`  
**Code:** `crypto_context.py` → `_fetch_stablecoin_supply`; `opus_macro.py` → `fetch_stablecoin_supply`  
**API key:** None

### Response

Time series of aggregate stablecoin market cap:

| Field | Description |
|-------|-------------|
| `date` | Unix day |
| `totalCirculating.peggedUSD` | Total USD-pegged stablecoin supply |

BereBank exposes:

- **`stablecoin_supply_usd`** — latest total  
- **`stablecoin_supply_change_pct`** — vs ~31 days ago  
- **`stablecoin_supply_by_day`** — full series (internal, for GTP56Sol history)  
- **`crypto:stablecoin_usd`** — same series persisted in `opus_macro_series` by the Opus harvest  

### Limits

- Free, no auth  
- Cache: **15 min**  

### Available but not integrated

DeFiLlama has TVL, chain-level stablecoins, protocol data — useful for macro liquidity research.

---

## FRED — St. Louis Fed macro history (free)

**URL:** `https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}`  
**Code:** `opus_macro.py` → `fetch_fred_series`  
**API key:** None (the CSV graph endpoint needs no registration)

Added for the Opus engine, which learns its weights from how features behaved on
past days and therefore needs macro data **with history**, not a live reading.
FRED also covers the gap where Twelve Data's Pro plan returns 404 for `US10Y`
and `VIX`.

### Series harvested

| FRED id | Opus series id | Content | History |
|---------|----------------|---------|---------|
| `DGS2` | `fred:us2y` | 2-year treasury yield, daily close | 1976 → today |
| `DGS10` | `fred:us10y` | 10-year treasury yield, daily close | 1962 → today |
| `VIXCLS` | `fred:vix` | CBOE VIX daily close | 1990 → today |

### Response

Two-column CSV (`observation_date,VALUE`, historically `DATE`). Missing
observations are written as `.` and skipped by the parser.

### Use in BereBank

- Persisted in `opus_macro_series`, refreshed by the Opus harvest (every 6h,
  upsert-only so unchanged days are not rewritten).
- Feeds the Opus macro-beta features: each market's rolling sensitivity to VIX
  and to the 10-year yield, plus the yield-curve reading shown on the Opus page.
- Not used by Analyze, KimiK3, Fable5 or GTP56Sol — those keep their Twelve Data
  live macro path unchanged.

### Limits

- Free, no auth, no documented rate limit for CSV graph downloads  
- Fetched at most once per harvest cycle per series; failures are non-fatal  

---

## RSS news feeds (free)

**Code:** `backend/app/services/rss_aggregator.py`  
**Configuration:** Admin → **RSS news feeds** (add URLs; default feeds seeded on first run)

### Default feeds

| URL | Name |
|-----|------|
| `https://www.coindesk.com/arc/outboundfeeds/rss` | CoinDesk |
| `https://cointelegraph.com/rss` | Cointelegraph |

BankManager can add any RSS/Atom URL.

### Data extracted per article

| Field | Description |
|-------|-------------|
| `title` | Headline |
| `body` | Plain text (HTML stripped) |
| `url` | Canonical link |
| `source_name` | Feed name |
| `published_at` | UTC datetime |
| `external_id` | GUID or URL hash |

### Matching

Articles are linked to markets when title/body matches:

- Ticker regex (e.g. `BTC`, `AAPL`)  
- Asset name from market catalog  

**No sentiment score** — raw text only. Analysis engines do not consume news today; `get_news` / MCP expose articles for AI reading.

### Polling

- **Every 3600 s** (1 hour)  
- Stored in `news_articles` + `news_article_markets`  
- API cache: **5 min**  

---

## Configuration reference

### Admin UI (BankManager)

| Setting | Provider |
|---------|----------|
| Twelve Data API key | Twelve Data Pro |
| Coinglass API key | Coinglass Hobbyist |
| Bitvavo API key / secret | Stored only (unused for market data) |
| RSS feed URLs | Any RSS source |

### Environment variables

| Variable | Provider |
|----------|----------|
| `BEREBANK_COINGLASS_API_KEY` | Coinglass (fallback if DB empty) |

Twelve Data has **no env fallback** — must be set in Admin.

---

## What is not persisted

BereBank **re-fetches on demand** (with in-memory cache) for:

- All supplementary context (TD macro, crypto macro, Coinglass)  
- Fear & Greed, stablecoin, dominance series  
- Coinglass funding map  

**Candle import/export** (`/admin/candle-history/export|import`) includes **OHLCV + GTP56Sol backfill flags only** — not external macro/derivatives series.

**Persisted for Opus** (the exception to the above): the Opus harvest stores
daily FRED yields and VIX, the full Fear & Greed history, stablecoin supply and a
per-coin funding snapshot in `opus_macro_series`, plus its learned calibration
and daily recommendation snapshots. Those three tables have their own streaming
gzip-NDJSON transfer at `/admin/opus-dataset/export|import` (optionally including
candles), so a fresh install can be seeded from a development machine.

Coinglass funding/OI **4h history** is still not harvested; Opus appends the
current funding snapshot one day at a time instead.

---

## Suggested additions by subscription

You already pay for **Twelve Data Pro** and **Coinglass Hobbyist**. Highest-value integrations not yet built:

| Priority | Source | Data | Effort |
|----------|--------|------|--------|
| 1 | Coinglass | `/pairs-markets?symbol=` for liquidations + long/short split | Low — one call per cached coin |
| 2 | Twelve Data | `/statistics` or `/profile` for valuation features | Low — per stock |
| 3 | Twelve Data | Pre/post-market `/time_series` | Medium |
| 4 | Coinglass | Funding/OI **4h history** for GTP56Sol backfill | Medium |
| ~~5~~ | ~~FRED~~ | ~~Historical **DGS10** / VIX if TD stays null~~ | **Done** — harvested for Opus (`fred:*`) |
| 6 | RSS + NLP | Sentiment score on matched headlines | Medium — no vendor required |
| 7 | CoinGecko Pro | Historical BTC dominance series | Low — paid tier |

---

## Related documents

- [marketdata.md](marketdata.md) — how external data flows through BereBank APIs and analysis engines  
- [AGENTS.md](../AGENTS.md) — MCP tools for AI agents  
- [Twelve Data pricing](https://twelvedata.com/pricing)  
- [Coinglass pricing](https://www.coinglass.com/pricing)  
- [Coinglass API docs](https://github.com/coinglass-official/coinglass-api-docs)
