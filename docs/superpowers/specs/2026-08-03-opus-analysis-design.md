# Opus analysis — design spec

Date: 2026-08-03
Status: implemented

## Goal

Answer the question the four existing engines do not: **of everything I could
hold for the next day to four weeks, what should I buy or sell right now, and is
the edge bigger than the fees?** Opus therefore produces a *ranking* of every
crypto, stock, fund and commodity as its primary output, expressed in euros
after real Bitvavo fees, and exposes it through REST, its own web page and three
MCP tools. It is added as a fifth, fully isolated engine: no existing function,
table, endpoint, tool or page changes behavior.

## Decisions

- **Cross-sectional, not absolute.** Every feature is turned into a percentile
  z-score *versus peers in the same peer group on the same day*, so market-wide
  beta drops out and "bullish" means "better than the alternatives I could hold
  today". Peer groups are `crypto`, `stock` and `other` (funds and commodities
  share one group so it is never too thin to rank).
- **Learned weights, not chosen weights.** A weight is the shrunk walk-forward
  information coefficient (IC) of that feature against forward returns over the
  stored daily panel, estimated per peer group, per horizon and per regime.
  Features whose IC sign is statistically indistinguishable from noise get
  weight zero automatically.
- **Euro-denominated edge.** The composite score is mapped through a calibrated
  score-to-return table into `expected_return_pct`, then reduced by the calling
  user's real round-trip fee. Over a 1-day horizon most edges do not clear a
  taker round trip at all — Opus says so instead of inventing a trade.
- **Tradability and crowding gates.** Liquidity floor, stale-data gate,
  market-hours awareness (a closed exchange is offered as a limit order) and a
  per-peer-group cap on the highlighted basket, so the shortlist is not the same
  bet ten times.
- **Honest live track record.** Opus snapshots its own daily recommendations and
  grades them once the horizon has passed, next to the walk-forward backtest the
  other engines report.
- **Priors only as a fallback.** Young installs and the single-market
  walk-forward replay use a published-research prior weight vector, and the
  payload always states whether the weights in use were learned.

## Data

### Existing, untouched

`market_candles` (shared daily candle store, ~214k rows over 561 markets since
2021-02) and the live price/market-hours services. Opus reads them; the candle
harvest, its transfer endpoints and every other engine keep their behavior.

### New: `opus_macro_series`

One narrow `(series_id, day, value)` table for every external or derived daily
series, which keeps the transfer format trivial. Harvested by
`services/opus_macro.py`, all free and key-less, all best-effort (a failing
source simply yields no series):

| Series | Source | History |
| --- | --- | --- |
| `fred:us2y`, `fred:us10y`, `fred:vix` | FRED `fredgraph.csv` (DGS2, DGS10, VIXCLS) | 1962 / 1976 / 1990 → today |
| `crypto:fear_greed` | Alternative.me `?limit=0` | 2018 → today |
| `crypto:stablecoin_usd` | DeFiLlama stablecoin chart | 2017 → today |
| `funding:{COIN}` | Coinglass bulk funding snapshot | appended daily |

FRED also closes the documented gap where Twelve Data's Pro plan returns 404 for
`US10Y` and `VIX`. Asset-class breadth, the equal-weight peer index and every
per-market beta are computed from `market_candles`, so they have full history
from day one without any provider.

### New: `opus_calibration`, `opus_recommendations`

One calibration row per `(engine_version, peer_group, horizon, regime)` holding
the JSON weight vector, the score-to-return bins and the IC diagnostics; one
recommendation row per `(day, market, horizon)` with the published advice and,
later, the realized forward return. Both created by the existing `create_all`.

## Components

### Features — `backend/app/services/opus_features.py`

Pure functions, single pass per market, every value at bar *i* using only bars
up to *i*. 23 features: volatility-adjusted momentum over 21 and 63 bars and
their difference (acceleration), 5-day and 1-day reversal, distance to the
50-day mean in ATR units, signed ADX direction, RSI deviation, Bollinger and
20-day range position, volatility expansion and level, 63-day drawdown, volume
z-score, log euro turnover, beta / correlation / residual momentum versus the
peer index, sensitivity to VIX, the 10-year yield, crypto sentiment and
stablecoin supply, and perpetual funding.

The macro betas exist because a macro *reading* is identical for every market on
a given day and therefore says nothing about which one to hold; how strongly
each market *responds* to it does. Beta, correlation and the macro
sensitivities come from one rolling regression per pair, updated incrementally.

`cross_section()` rank-scores each feature within the peer group for one day;
`time_series_z()` is the single-market fallback used by the walk-forward replay.

### Calibration — `backend/app/services/opus_calibration.py`

One strictly forward pass over the stored panel, pure stdlib (no numpy):

- For each day, peer group and horizon (1 / 5 / 21 bars), the daily
  cross-sectional Spearman IC of every feature against the clipped forward
  return is accumulated. Running statistics use only days *before* the day being
  scored, so the composite scores collected along the way are genuinely
  out-of-sample; their IC and hit rate become the reported walk-forward
  diagnostics.
- Significance is overlap-corrected: daily observations of a 21-bar forward
  return are largely the same observation seen repeatedly, so the effective
  sample size is `count / overlap`. Without that a 4-week signal would look
  about five times more significant than it is.
- Weight = `mean_IC × |t| / (|t| + 2)`; below one standard error a feature earns
  nothing, and the vector as a whole only counts as *learned* once at least two
  features clear two standard errors and at least three carry weight —
  otherwise the prior vector is used. With 23 candidate features,
  one-standard-error gating alone would let a purely random panel produce
  confident-looking weights.
- Learned vectors keep 20% of the prior; regime vectors are shrunk halfway
  toward the pooled estimate so small buckets stay sane.
- The score-to-return map is a set of fixed bins over the normalized composite,
  filled with the average forward return per bin and monotonically smoothed.
  That is what converts an abstract score into an `expected_return_pct`.
- Regime = the peer index above or below its own 50-day mean (`up` / `down`),
  plus a pooled `all` row that is used when the regime row is not reliable.

### Scoring — `backend/app/services/opus_analysis.py`

Pure functions over candle lists and plain dicts (context is passed in, exactly
as with `analyze_fable5`), so the same code serves the nightly job, the request
path and the unit tests:

1. Composite = weighted sum of available feature z-scores, rescaled by the share
   of weight actually present, so a market with partial data is neither
   penalized nor flattered.
2. `expected_return_pct` = score-to-return map lookup (peer-relative alpha) plus
   the regime's average peer return scaled by this market's beta, capped at 2.
3. `net_edge_pct` subtracts the round trip for the calling user's fee tier;
   `net_edge_limit_pct` does the same with maker fees and
   `suggested_order_type` becomes `limit` when only the maker path is
   profitable. Selling something already held pays one leg, not two.
4. `conviction` = net edge / expected move over the horizon — an information
   ratio — squashed into `buy_score` and `sell_score` (0..100), keeping the
   template's "high on both sides = contested" semantics.
5. `action` ∈ `strong_buy | buy | hold | reduce | sell` from those scores;
   `score` (−100..+100), `direction`, `confidence` and per-feature
   `contributions` keep the analysis-page template contract.
6. Guards: a volatility floor (a rounding error must not divide into infinite
   conviction) and a low-volatility cut-off that drops instruments which cannot
   move enough to pay a fee over these horizons — euro stablecoins and
   money-market-like bond funds.

`finalize_row()` applies the ranking gates (median euro turnover ≥ 25k, staleness
of 3 bars for crypto and 6 for exchange-traded markets, `market_open`), and
`select_basket()` builds the diversified shortlist (10 rows, capped per peer
group).

### Persistence and orchestration — `backend/app/services/opus_store.py`

All database access plus `OpusHarvestService`, started next to the candle
harvest in `main.py`: refresh the macro series, recalibrate (at most daily),
snapshot today's rankings, grade snapshots whose horizon has passed, prune past
400 days. Scoring loads a 260-day panel; calibration loads up to 1500 days.
`live_track_record()` reports the hit rate of published advice once at least 20
graded samples exist.

### Dataset transfer — `backend/app/services/opus_dataset_transfer.py`

The Opus dataset is far larger than the candle history it builds on, so a single
JSON document (as `/admin/candle-history` uses) would mean holding hundreds of
megabytes in memory on both ends. Transfer is therefore **gzip NDJSON**: a
header line, then one compact record per row, streamed out at constant memory
and read back in 5000-row batches. Candles are optional
(`include_candles=true`), so a fresh production install can be seeded from a
development machine in one file. Plain or gzip input is accepted; import is an
upsert, so re-importing changes nothing but the rows that actually differ.

### API

Declared before `/{market}/...` and each with its own cache, per the isolation
contract:

- `GET /markets/opus-rankings?horizon=1d|1w|4w&asset_class=&side=&limit=` — the
  headline output: every market with scores, ranks, action, expected return, net
  edge, conviction, tradability and suggested order type, plus the basket,
  regimes, macro backdrop and whether the engine is calibrated.
- `GET /markets/opus-outlooks?horizon=` — the template's batch summary
  projection.
- `GET /markets/{market}/opus-analysis?range=&horizon=` — detail with live
  candles, the feature table, calibration provenance, the walk-forward track
  record and the live snapshot record.
- `GET /admin/opus-dataset/status|export`, `POST /admin/opus-dataset/import`,
  `POST /admin/opus-dataset/recalibrate`.

The shared scoring pass is cached 15 minutes (Opus scores completed daily bars,
so a fresh pass per quarter hour is plenty) and runs in a threadpool.

### MCP tools

- `get_opus_rankings(horizon, asset_class, side, limit)` — the ranked buy or
  sell list with the diversified basket.
- `get_opus_analysis(market, range, horizon)` — per-market detail, feature
  table, calibration provenance, both track records.
- `get_opus_portfolio_advice(horizon)` — joins the ranking to `get_portfolio`:
  an exit opinion per holding and buy candidates sized against free cash and the
  EUR 5 minimum. Read-only; it never places an order.

Fees follow the connected user's own tier, so the tools and the web page agree
to the cent.

### Frontend — `frontend/src/pages/OpusAnalysisPage.tsx`

Follows the analysis page template with one deliberate extension: the picker
view *is* the ranking board.

- `/opus-analysis`: horizon tabs, Buy/Sell toggle, asset-class filter, sortable
  columns (rank, market, score, expected return, net edge, conviction, action,
  live price), the macro strip, the highlighted basket, and inline hints when a
  row is stale, illiquid, too quiet or only tradable as a limit order.
- `/opus-analysis/:market`: verdict hero with the conviction gauge, euro chips
  (expected return, net edge, alpha, suggested stop), the cross-section card
  (peer group, peers, regime, data age), the expandable feature table
  (percentile, weight, IC, contribution, plain-language explanation as a
  tooltip), calibration provenance, both track records, disclaimer.
- Additive wiring only: routes, `nav.opus`, mobile More sheet, analysis
  cross-links, a Trade-page button, `Opus*` types, `opusAnalysis.*` in `en.json`
  and `nl.json`, and an `OpusDatasetTransfer` card on the admin page.

## Testing

- `backend/test_opus_analysis.py` — feature math, cross-sectional ranking,
  calibration on a synthetic panel with a planted signal (it must recover the
  planted sign and refuse to learn from noise), the fee and gate logic, ranking
  and basket diversification, macro parsing, and the store including the live
  track record.
- `backend/test_opus_dataset_transfer.py` — export format, full round trip with
  and without candles, idempotent re-import, a 60k-row dataset streamed in
  chunks, and the empty / headerless / mis-versioned / corrupt / truncated error
  paths.
- Smoke scripts against the real development database: `_opus_smoke.py`
  (harvest → calibrate → rank), `_opus_api_smoke.py`, `_opus_mcp_smoke.py`,
  `_opus_transfer_smoke.py`.
- Isolation smoke test: every existing backend suite and the frontend
  `tsc -b && vite build` plus vitest pass unchanged.

## Constraints and notes

- Opus never imports another analyzer's module and no analyzer imports Opus.
- No new paid provider and no extra load on the existing ones: the new sources
  are free and key-less, and the Coinglass snapshot reuses the one bulk funding
  call already made.
- The engine deliberately reports "no trade" when nothing clears the fees, which
  over a 1-day horizon is the common case.
- Educational simulation, not financial advice.
