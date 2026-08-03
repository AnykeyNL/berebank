# de BereBank — Guide for AI Agents

This document explains what **de BereBank** is, what participants are trying to achieve, and how you can help them through the built-in **MCP server**. Read this before assisting a user with trading, portfolio analysis, or strategy development.

## Purpose

**de BereBank** is a **simulated exchange** where participants practice investing with **paper money** against **live market data**. No real money moves and no real orders are sent to external brokers — only the prices are real.

The learning goal is practical: **each participant develops an investment strategy and tries to grow their account value over a set period**. At the end, whoever has the **highest total account value** wins. Rankings are shown on the in-app **Leaderboard**, which compares all active traders by:

- **Cash** — EUR balance plus funds reserved for open limit buy orders
- **Assets** — holdings valued at the current live last price
- **Total** — cash + assets (this is the score that matters)

Participants start with an EUR balance set by the **BankManager** (administrator). Strategy, timing, asset mix, and execution are entirely up to each trader.

### Using AI is encouraged

Investigating **how AI and AI agents can help develop and execute an investment strategy** is not only allowed — it is **recommended**. Typical ways you can help:

- Research markets (crypto, stocks, funds) and summarize trends from live data
- Analyze the user's portfolio, P&L, and fee tier
- Compare performance to the leaderboard
- Propose and explain strategies (diversification, momentum, mean reversion, DCA, etc.)
- Execute trades on the user's behalf **when they explicitly enable MCP trading**

Always make clear that this is a **simulation** for learning. Outcomes here do not guarantee real-world results.

## What can be traded

All instruments are quoted in **EUR** (`{TICKER}-EUR`):

| Asset class | Source | Trading hours |
| --- | --- | --- |
| **Crypto** (~430 markets) | Bitvavo live WebSocket | 24/7 |
| **Stocks** | S&P 100 | Exchange hours only |
| **Funds** (ETFs) | Popular US ETFs | Exchange hours only |
| **Commodities** | Gold (XAU), silver (XAG), platinum (XPT), palladium (XPD), WTI, Brent (XBR) and Urals crude oil | Forex hours (roughly 24/5, closed weekends) |

**Stocks, funds and commodities:** market orders are rejected while the market is closed (evenings and weekends for stocks/funds, weekends for commodities). Limit orders can be placed anytime and may fill when trading resumes. Crypto has no market-hours restriction.

## How trading works (simulation rules)

Understanding these rules helps you give accurate advice and place valid orders:

- **Market orders** — fill immediately at live bid (sell) or ask (buy); pay the **taker** fee
- **Limit orders** — rest until the live price crosses the limit; pay the **maker** fee when filled. Buy limits reserve EUR (including fee) upfront; sell limits lock the asset amount
- **Stop-loss orders** — sell only: rest until the live bid drops to the trigger price, then sell at the live bid (the fill can be below the trigger on a price gap); pay the **taker** fee. The trigger price must be below the current price at placement; the asset amount is locked while the order rests
- **Fees** — Bitvavo Category A maker/taker tiers based on trailing **30-day executed volume** (base tier: 0.15% maker / 0.25% taker). Fees are charged in EUR
- **Minimum order** — EUR 5 (same as Bitvavo)
- **Amounts and prices** — decimal numbers; API and MCP responses serialize them as **strings** (e.g. `"1234.56"`)

Orders placed via MCP use the **same engine** as the web app: identical validation, prices, fees, and balance checks.

## MCP server — what you can do

de BereBank exposes an [MCP](https://modelcontextprotocol.io) server so assistants like Claude, ChatGPT, or Cursor can connect directly to the user's account.

**Endpoint:** `{origin}/mcp` (e.g. `http://127.0.0.1:8000/mcp` locally, or `https://<domain>/mcp` in production)

**Authentication:** OAuth 2.1 — the user signs in with their BereBank email/password and clicks *Allow access*. No API keys. Access can last up to 30 days (refresh tokens); disabling the account revokes access immediately.

### Read tools (always available to active users)

| Tool | Use for |
| --- | --- |
| `list_markets` | Browse all markets with live last/bid/ask, 24h change, volume. Filter by `asset_class` (`crypto`, `stock`, `fund`, `commodity`) or symbol substring (e.g. `filter="BTC"`) |
| `get_candles` | OHLCV candles for charting and technical analysis. Optional `range`: `1h`, `1d` (default), `1w`, `30d`, `90d`, `180d`, `365d`; the bar interval scales with the range (1-minute up to daily bars) |
| `analyze_market` | Technical analysis over a past `range` (`1d`, `1w`, `30d` default, `90d`, `180d`, `365d`): five strategies — trend (SMA/EMA crossovers), RSI-14, MACD, volatility (Bollinger Bands + ATR with a suggested stop-loss), support/resistance + volume — each with a bullish/bearish/neutral signal, reason, and explanation. Same engine as the web app's Analyze page |
| `get_kimi_analysis` | KimiK3 direction outlook for a market (same ranges as `analyze_market`): eight price strategies — the five base strategies plus ADX trend strength, dual-horizon momentum and a slow stochastic — extended per asset class (crypto: Fear & Greed sentiment momentum, BTC-aware dominance/stablecoin liquidity, Coinglass funding level and 4h funding momentum, price-confirmed open interest on 1h/4h/24h windows, long/short positioning, liquidation flows; stocks: VIX, yield curve, sector relative strength, earnings-proximity brake, insider flow; funds/commodities: safe-haven aware VIX/yield logic, crypto macro signals for IBIT) blended with regime-aware weights into one bullish/bearish/neutral verdict with a -100..+100 score, a `buy_score` and `sell_score` (0..100 shares of active weight voting bullish resp. bearish; higher buy_score = more evidence favors buying, high on both sides = contested market), confidence level, market regime, and per-strategy votes. Includes a `track_record` (hit rate of past outlooks on that market over the following 5 days) once enough daily history has been harvested. Same engine as the web app's KimiK3 page |
| `get_gtp56sol_analysis` | Independent historical-pattern forecast for a market. Optional `horizon`: `1d`, `1w` (default), or `1m`, meaning 1, 5, or 21 forward trading-session bars. Returns Up/Sideways/Down probabilities, confidence, similar-history and walk-forward evidence; sparse assets may use a bounded same-asset-class fallback pool. |
| `get_fable5_analysis` | Fable5 direction outlook for a market (same ranges as `analyze_market`): fixed-weight signals — the five strategies plus dual-horizon momentum, a slow stochastic, and ADX trend strength, extended per asset class (crypto: Fear & Greed sentiment momentum, liquidity, Coinglass funding, price-confirmed open interest, long/short positioning, liquidation flows; stocks: VIX, yield curve, sector relative strength, earnings-proximity brake; funds/commodities: macro signals, safe-haven aware for precious metals) — blended into one bullish/bearish/neutral verdict with a -100..+100 gauge score, a `buy_score` and `sell_score` (0..100 shares of signal weight voting bullish resp. bearish; higher buy_score = more evidence favors buying, high on both sides = contested market), weighted-agreement confidence, and per-strategy votes. Includes a `track_record` (hit rate of past outlooks over the following 5 days) once enough daily history has been harvested. Same engine as the web app's Fable5 page |
| `get_opus_rankings` | **The "what should I buy or sell right now?" tool.** Where the other engines score one market against absolute thresholds, Opus ranks every crypto, stock, fund and commodity against its peers *on the same day* and answers in euros after fees. Optional `horizon`: `1d`, `1w` (default) or `4w`; `asset_class`; `side` (`buy` default or `sell`, which decides the ordering); `limit` 1–200. Each of 23 features (volatility-adjusted momentum over two horizons, short-term reversal, distance to the 50-day mean in ATR units, signed ADX, RSI, Bollinger and 20-day range position, volatility expansion and level, drawdown, volume surge, turnover, beta/correlation/residual momentum versus the peer index, sensitivity to VIX, the 10-year yield, crypto sentiment and stablecoin supply, and perpetual funding) is rank-scored within the market's peer group, then weighted by its **learned** weight: the shrunk walk-forward information coefficient against forward returns, estimated per peer group, horizon and market regime, with statistically insignificant features automatically zeroed. The composite maps through a calibrated score-to-return table into `expected_return_pct`; `net_edge_pct` subtracts the real round-trip Bitvavo fee for *your* tier, `net_edge_limit_pct` does the same with maker fees and `suggested_order_type` becomes `limit` when only the maker path pays. `buy_score`/`sell_score` (0..100) are that fee-aware edge per unit of expected move, and `action` is one of strong_buy, buy, hold, reduce, sell. Also returns `basket` (a diversified shortlist capped per peer group), the current `regimes`, the `macro` backdrop and whether the engine is `calibrated`. Rows flagged `stale`, `low_volatility` or not `liquidity_ok` rank last and are never advised as buys. Over a 1-day horizon most edges do not clear a taker round trip — Opus says so rather than inventing a trade. Same engine as the web app's Opus page |
| `get_opus_analysis` | Opus detail for one market (`range` as `analyze_market`, `horizon` `1d`/`1w`/`4w`): the full feature table with each feature's peer percentile, learned weight, information coefficient and contribution; the fee-aware recommendation with expected return, net edge, alpha versus its peer group, expected move and a suggested stop; the cross-section context (peer group, number of peers, regime, data age); calibration provenance (how many days and samples the weights were learned from, and the walk-forward IC and hit rate); plus **two** track records — the walk-forward backtest and the live hit rate of the recommendations Opus actually published |
| `get_opus_portfolio_advice` | Opus ranking joined to the user's own account (`horizon` `1d`/`1w`/`4w`): an exit opinion and sell rank for every holding, buy candidates from the diversified basket the user does not hold yet, and a suggested EUR allocation sized against free cash and the EUR 5 minimum order. Read-only — it never places or cancels anything |
| `get_news` | Recent news for any market: RSS-matched articles (crypto and all assets) plus Twelve Data press releases for stocks/funds. Optional `limit` 1–10 |
| `get_portfolio` | Cash, reserved funds, holdings with live valuation, total account value, current fee tier |
| `get_portfolio_history` | Hourly snapshots of the past 30 days: total account value and distinct assets held, oldest first — for charting performance over time |
| `list_orders` | Open, filled, or cancelled orders (newest first, max 200) |
| `list_trades` | Executed trades (newest first, max 200) |
| `get_trade_history` | Full trade history with **FIFO realized P&L** on sells (`pnl_eur`, `pnl_pct`, `held_seconds`) |
| `get_leaderboard` | Competition ranking of all active traders by total account value (cash, assets, total, trade count); the connected user's entry is marked `is_you` |

### Trading tools (opt-in per user)

| Tool | Use for |
| --- | --- |
| `place_order` | Buy or sell via `market` or `limit` order |
| `cancel_order` | Cancel an open order by ID |

Trading tools require the user to enable **"Allow trading via MCP"** in their profile (**MCP access** section). This is **off by default** and checked on every call — turning it off takes effect immediately.

### `place_order` parameters

```
market        — e.g. "BTC-EUR", "AAPL-EUR", "SPY-EUR"
side          — "buy" or "sell"
order_type    — "market", "limit" or "stop_loss"
amount        — base asset quantity (decimal string)
amount_quote  — EUR amount; market orders only (use exactly one of amount or amount_quote)
limit_price   — required for limit orders (together with amount)
trigger_price — required for stop_loss orders (together with amount); must be below the current price
```

## Suggested workflow for strategy development

When helping a user compete on the leaderboard:

1. **Baseline** — call `get_portfolio` and `get_trade_history` to understand starting position and past decisions
2. **Scan opportunities** — the fastest route is `get_opus_rankings` (or `get_opus_portfolio_advice`, which already joins the ranking to the user's holdings and cash), since it ranks every market by fee-aware expected edge for the chosen horizon; then use `list_markets` and `get_candles` for context and run `analyze_market`, `get_kimi_analysis`, `get_fable5_analysis` or `get_gtp56sol_analysis` on the shortlist for an independent second opinion
3. **Form a strategy** — discuss goals, risk tolerance, time horizon, and constraints (fees, minimum order size, stock market hours)
4. **Execute deliberately** — only call `place_order` when the user has enabled MCP trading and confirms the trade
5. **Review regularly** — track `total_value_eur`, open orders, and realized P&L; adjust as the competition period progresses
6. **Compare** — call `get_leaderboard` to see the user's rank and the gap to competitors, and factor that into strategy (e.g. how much ground to make up)

## Example prompts you can support

- *"How is my portfolio doing? What's my total account value and fee tier?"*
- *"Where am I on the leaderboard, and how far behind is the number one?"*
- *"Which crypto markets gained the most in the last 24 hours?"*
- *"Show my trade history with profit/loss — what was my best and worst trade?"*
- *"I'm overweight in Bitcoin. Suggest a rebalancing plan across crypto and ETFs."*
- *"What are the ten best buys for the next week, after fees?"*
- *"Anything in my portfolio I should get out of? Use the Opus sell ranking."*
- *"Why does Opus like XLF-EUR — which features are carrying that score?"*
- *"Place a limit buy for 0.01 BTC at €95,000"* (requires MCP trading enabled)
- *"Compare my strategy to a simple buy-and-hold in SPY-EUR using candle data."*

## Important constraints

- You see **only the connected user's** account in detail — `get_leaderboard` shows other traders' totals (cash, assets, total) but never their individual positions or orders
- **Never** place or cancel orders unless the user has enabled MCP trading and asked you to trade
- Stock, fund and commodity **market orders fail while the market is closed** — suggest limit orders or waiting
- High-frequency churn increases **fees** and can lower net returns — factor fees into strategy advice
- The Opus tools only recommend a trade whose edge clears the round-trip fee, so an empty buy list (common on the 1-day horizon) is a real answer, not a failure — say so instead of picking the least bad row
- This is **educational simulation** — not financial advice for real investing

## Connecting the MCP server

Users connect from **Profile → MCP access** or the **AI** page in the web app. The server URL and setup steps for Claude, ChatGPT, and Cursor are documented there.

For technical details (OAuth flow, tool implementation), see the [README](README.md) and [MCP design spec](docs/superpowers/specs/2026-07-19-mcp-server-design.md).
