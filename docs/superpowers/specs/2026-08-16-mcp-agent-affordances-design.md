# MCP agent affordances — design

**Date:** 2026-08-16
**Status:** Implemented

## Goal

The MCP server was built by exposing the REST layer a human web app already
used — its own module docstring says so. That works, but it leaves an
autonomous agent without four things a machine caller needs and a person does
not: a way to make a retry safe, a way to try an order before committing to it,
a way to reason about time when the exchange is shut, and responses that fit in
a context window. This work adds them, in three phases, without changing what
an order costs or how it fills.

External feedback from a tester running an agent against the live server drove
the list. Every point checked out against the code.

## Phase 1 — Context budget and safety

### `get_outlooks` — many markets, many engines, one call

The web app already had bulk outlook endpoints (`/markets/kimi-outlooks` and
friends) that score the whole universe from stored daily candles behind a 900 s
cache. They were never exposed over MCP, so an agent comparing four markets
across four engines made sixteen per-market calls, each fetching live candles.
`get_outlooks(markets, engines, asset_class, limit)` projects the same cached
handlers: five engines (`technical`, `kimi`, `fable5`, `opus`, `gtp56sol`),
verdict fields only, no extra provider requests.

The numbers can differ slightly from a per-market call, because the bulk
endpoints run on daily candles over the full stored history while
`get_kimi_analysis(range="30d")` uses 4 h bars for crypto. Batch is for
screening and consensus; per-market is for depth. The tool docstring says so.

### `verbose` on the per-market analysis tools

`backend/app/services/payload.py` holds one trimmer that drops `candles` and
empties every `strategies[*].series`, keeping signals, values, explanations and
the whole outlook block. REST keeps `verbose=true` as its default because the
frontend draws charts from that payload; **MCP defaults to `verbose=false`**.
Measured on `analyze_market` for BTC-EUR: 3.9 kB against 177 kB.

### `client_order_id` — idempotency

`orders.client_order_id VARCHAR(64)` with a unique index on
`(account_id, client_order_id)`. On a collision the engine **returns the
existing order** with `duplicate: true` rather than raising: the scenario is a
lost response to a placement that succeeded, and the caller wants the order
that exists, not an error.

### `validate_only` — dry run

All three placement branches already ran their checks before any mutation, so
the dry run is a `return preview` at that point — no savepoint, no rollback.
One helper (`_check_holding`) was extracted because the sell-side balance check
lived inside `_debit_holding`. The preview reports the price and its basis, the
amount, fee and fee rate, what would be reserved or locked, the resulting
balance and whether the order would fill immediately. An invalid order fails
with the identical message a real placement would give.

### `get_account_status` — capability call

`mcp_trading_enabled` was only visible over REST `/auth/me`, so an agent could
only discover it by having an order rejected. The tool reports it along with
the unlocked tool names, OAuth scopes, the fee tier with its 30-day volume, the
EUR 5 minimum and the server's UTC time. It is named in the FastMCP
`instructions` so an agent meets it before drafting a trade.

### Precision in `list_markets`

Bitvavo's `/markets` and `/assets` were being read for `min_quote` alone.
`tick_size`, `amount_decimals` (with the asset's `decimals` as fallback) and
`min_order_base` now come along. `pricePrecision` was intended but is null on
the live API, so `tickSize` is used instead. For Twelve Data instruments these
are `null`; every row also carries what the **engine** enforces regardless of
venue — `amount_quantum` (1e-8) and `min_order_eur` (5) — so no client has to
guess and no existing order becomes invalid.

Decimals are serialized through `plain_decimal()`: tick sizes reach 1e-10,
where `str(Decimal)` would emit `1E-10`.

## Phase 2 — Trading calendar and order expiry

### `market_calendar` facade

New dependency `exchange_calendars`: XNYS for stocks and funds, the built-in
24/5 calendar for commodities, nothing for crypto. It pulls in pandas and
numpy, which the backend otherwise does without (everything is `Decimal` and
plain Python), so `backend/app/services/market_calendar.py` is a deliberately
thin facade — `session_state`, `advance_sessions`, `sessions_between`,
`note_disagreement` — with cached calendar objects. Nothing outside that file
imports pandas, so the library stays replaceable.

Calendars are built two years ahead and rebuilt when a query comes within 90
days of the end, so an expiry can never fall off the edge.

### Hours as data

`MarketOut` gains `next_open` and `next_close` (null for crypto).
`GET /markets/hours` / `get_market_hours(market | asset_class)` adds
`is_open`, `current_session_end` and the exchange timezone.

`market_open` still comes from Twelve Data, which sees halts and unscheduled
closures the calendar cannot; the calendar supplies only timestamps. When the
two disagree a throttled warning is logged (once per 15 minutes per asset
class). That also surfaces the pre-existing
`bool(quote.get("is_market_open", False))` gap, where a missing field reads as
closed.

### Expiry on orders

Columns `time_in_force VARCHAR(4)` (`gtc` default, `day`, `gtd`), `expires_at`
and `expires_after_sessions`. `place_order` accepts `time_in_force` plus either
`expires_at` (ISO 8601) or `expires_in_sessions` (1–250).

**The intention is resolved to a concrete UTC moment at placement** and
returned, so the agent can see what was actually agreed. Expiry counts in
trading sessions, which is the only reading that survives a weekend: an NYSE
order placed Saturday evening with `expires_in_sessions=2` runs out at
Tuesday's close, not forty wall-clock hours later. `day` is one session. A
crypto session is a 24-hour day. Contradictions are rejected rather than
guessed at — `gtc` with an expiry, `day` with an expiry, `gtd` without one,
both forms at once, a past moment, or any expiry on a market order.

New status `expired`, released exactly as a cancellation is: `_release_order()`
is now shared by both, so a refund can never drift between the two paths.

The sweeper is a background task
(`backend/app/services/order_expiry.py`, modelled on `snapshots.py`, every 60 s
under the same `trade_lock` the matcher takes). A hook in `match_limit_orders`
would not do: a stock stops ticking the moment its exchange closes, and that is
precisely when a day order must expire. Between sweeps, `_try_fill_resting_order`
refuses to fill an order that has already lapsed.

## Phase 3 — Historical leaderboard

`GET /leaderboard/history` / `get_leaderboard_history(days=30, interval="day")`.
`portfolio_snapshots` already holds hourly totals for every active non-admin
account, written in one pass with an identical `created_at` and kept 180 days,
so ranking a past moment is an ordering rather than a reconstruction. Per point:
the user's rank and total, the leader's total and the number of traders ranked.
A day reads as its last run of that day; `interval="hour"` keeps every run.

The snapshots hold only the total, so the cash/assets split and the trade count
stay point-in-time in `get_leaderboard`.

## Cross-cutting

**Timestamps.** `OrderOut` and the history points serialize through
`schemas.utc_iso()`. Everything is stored in UTC, but SQLite returns naive
datetimes where PostgreSQL keeps the zone, and a client scheduling around
`expires_at` should not have to know which database it is talking to.

**Migrations.** There is no Alembic; schema changes are additive `ALTER TABLE`
blocks in `migrate_schema()` in `main.py`, and the new columns and the unique
index follow that pattern.

## Testing

Standalone scripts with the existing `check()` helper: `test_payload_trim.py`,
`test_order_idempotency.py`, `test_market_calendar.py`, `test_order_expiry.py`,
`test_leaderboard_history.py`. `mcp_smoke_test.py` covers the new tools and
parameters end to end against a running server, including that a dry run
stores nothing, that a replayed `client_order_id` returns the first order, and
that a placed expiry comes back resolved and unchanged through `list_orders`.
