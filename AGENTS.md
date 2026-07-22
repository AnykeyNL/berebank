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

**Stocks and funds:** market orders are rejected while the exchange is closed (evenings, weekends). Limit orders can be placed anytime and may fill when trading resumes. Crypto has no market-hours restriction.

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
| `list_markets` | Browse all markets with live last/bid/ask, 24h change, volume. Filter by `asset_class` (`crypto`, `stock`, `fund`) or symbol substring (e.g. `filter="BTC"`) |
| `get_candles` | OHLCV candles for charting and technical analysis. Optional `range`: `1h`, `1d` (default), `1w`, `30d`, `90d`, `180d`, `365d`; the bar interval scales with the range (1-minute up to daily bars) |
| `analyze_market` | Technical analysis over a past `range` (`1d`, `1w`, `30d` default, `90d`, `180d`, `365d`): five strategies — trend (SMA/EMA crossovers), RSI-14, MACD, volatility (Bollinger Bands + ATR with a suggested stop-loss), support/resistance + volume — each with a bullish/bearish/neutral signal, reason, and explanation. Same engine as the web app's Analyze page |
| `get_news` | Recent news for any market: RSS-matched articles (crypto and all assets) plus Twelve Data press releases for stocks/funds. Optional `limit` 1–10 |
| `get_portfolio` | Cash, reserved funds, holdings with live valuation, total account value, current fee tier |
| `list_orders` | Open, filled, or cancelled orders (newest first, max 200) |
| `list_trades` | Executed trades (newest first, max 200) |
| `get_trade_history` | Full trade history with **FIFO realized P&L** on sells (`pnl_eur`, `pnl_pct`, `held_seconds`) |

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
2. **Scan opportunities** — use `list_markets` (filter by asset class or symbol) and `get_candles` for candidates; run `analyze_market` on shortlisted assets for trend, momentum, volatility, and support/resistance signals
3. **Form a strategy** — discuss goals, risk tolerance, time horizon, and constraints (fees, minimum order size, stock market hours)
4. **Execute deliberately** — only call `place_order` when the user has enabled MCP trading and confirms the trade
5. **Review regularly** — track `total_value_eur`, open orders, and realized P&L; adjust as the competition period progresses
6. **Compare** — remind the user to check the Leaderboard in the web app for ranking (MCP has no leaderboard tool; use portfolio total as their personal score)

## Example prompts you can support

- *"How is my portfolio doing? What's my total account value and fee tier?"*
- *"Which crypto markets gained the most in the last 24 hours?"*
- *"Show my trade history with profit/loss — what was my best and worst trade?"*
- *"I'm overweight in Bitcoin. Suggest a rebalancing plan across crypto and ETFs."*
- *"Place a limit buy for 0.01 BTC at €95,000"* (requires MCP trading enabled)
- *"Compare my strategy to a simple buy-and-hold in SPY-EUR using candle data."*

## Important constraints

- You see **only the connected user's** account — not other traders' positions (leaderboard totals are visible in the web UI only)
- **Never** place or cancel orders unless the user has enabled MCP trading and asked you to trade
- Stock/fund **market orders fail outside exchange hours** — suggest limit orders or waiting
- High-frequency churn increases **fees** and can lower net returns — factor fees into strategy advice
- This is **educational simulation** — not financial advice for real investing

## Connecting the MCP server

Users connect from **Profile → MCP access** or the **AI** page in the web app. The server URL and setup steps for Claude, ChatGPT, and Cursor are documented there.

For technical details (OAuth flow, tool implementation), see the [README](README.md) and [MCP design spec](docs/superpowers/specs/2026-07-19-mcp-server-design.md).
