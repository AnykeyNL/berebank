# Portfolio value chart — design

**Date:** 2026-07-22
**Status:** Approved

## Goal

Show a chart on the main portfolio page with, for the past 30 days:

- the user's **total account value** (cash + reserved EUR + holdings at live price)
- the number of **unique assets held**
- **buy/sell markers** at the times trades were executed

History is recorded going forward from deployment (no backfill); the chart fills
in over the first 30 days. Snapshots are retained for **180 days** even though
only 30 are shown, so longer ranges can be added later.

## Approach decision

Three options were considered:

- **A. Backfill from trades + historical candles, then snapshot** — most complete,
  but reconstruction is approximate (admin balance edits leave no ledger) and
  depends on external candle APIs.
- **B. Forward-only snapshots** *(chosen)* — simplest, exact values, no external
  API dependency; trade-off: the chart starts empty.
- **C. Compute on the fly per page view** — no new tables, but every view costs
  one external candle fetch per held market (Twelve Data rate limits, 1–3 s
  latency) and misrepresents admin balance changes.

## Data model

New table `portfolio_snapshots`:

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `account_id` | FK → accounts, indexed | |
| `total_value_eur` | Money | same valuation as leaderboard/portfolio |
| `asset_count` | int | distinct assets with positive amount, incl. assets locked in open sell orders |
| `created_at` | datetime (tz), indexed | snapshot time |

Created via `Base.metadata.create_all` (new table, no column migration needed).
Rows older than **180 days** are pruned by the snapshot job.

## Shared valuation service

The bulk valuation currently inlined in `GET /leaderboard` moves to
`app/services/valuation.py`:

```
compute_account_valuations(db) -> dict[account_id, AccountValuation]
  AccountValuation: cash_eur (balance + reserved EUR), assets_eur, asset_count, total_eur
```

Semantics identical to today's leaderboard: holdings and open-sell-order amounts
valued at the live last price; assets without a live price contribute 0 to value
but still count toward `asset_count`. Used by the leaderboard endpoint and the
snapshot recorder so all features agree on "total value".

## Snapshot recorder

`app/services/snapshots.py`, same service pattern as the RSS aggregator
(`start()` creates an asyncio task in the FastAPI lifespan, `stop()` cancels it):

- waits ~60 s after startup (lets live prices load), takes one snapshot pass,
  then aligns to the top of each hour
- a pass snapshots **all active non-admin users** in one bulk valuation, then
  prunes rows older than 180 days
- a pass is skipped when no live prices are available at all (feed not up yet),
  to avoid recording misleading cash-only dips

## API

`GET /portfolio/history` (authenticated, own account only): snapshots from the
last 30 days, oldest first:

```json
[{ "created_at": "...", "total_value_eur": "12345.67", "asset_count": 4 }, ...]
```

A single indexed SELECT; no external calls. The endpoint is fixed at 30 days.

## Frontend

New `PortfolioValueChart` component on `PortfolioPage`, placed directly under
the summary stat cards. Uses `lightweight-charts` (already a dependency):

- **Total value** — `AreaSeries` on the **right** price scale (primary, amber)
- **Unique assets held** — stepped `LineSeries` on the **left** price scale
  (subtle color), with a small legend under the chart
- **Trade markers** — fetches `GET /trades`, keeps trades inside the 30-day
  window, snaps each to its hourly snapshot point (trades before the first
  snapshot are dropped); buys render as up-arrows below the bar, sells as
  down-arrows above. Same-hour same-side trades collapse into one marker:
  single trade → "B BTC" style label, multiple → "3×B"
- data loads on mount and refreshes every 5 minutes
- fewer than 2 snapshot points → placeholder text ("history starts recording
  now") instead of an empty chart
- new i18n keys in `en.json` and `nl.json`

## Testing

Standalone verification script `backend/test_snapshots.py` (same pattern as
`test_stop_loss.py`, in-memory SQLite, mocked prices):

- valuation: cash + reserved + holdings + open-sell amounts; missing price → 0
  value but counted in `asset_count`
- recorder: writes one row per active non-admin account; skips inactive/admin;
  prunes rows older than 180 days
- history query: returns only own account's rows, last 30 days, oldest first
