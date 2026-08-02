# GTP56Sol analysis — design spec

Date: 2026-08-02
Status: backend engine, REST route, persistence integration, MCP tool, and frontend implemented

## Goal

Give every asset (crypto, stock, fund, commodity) an explainable,
historical-pattern forecast for the next day, next week, and next month. Each
forecast shows Up/Sideways/Down probabilities, conservative confidence, the
closest historical setups, and walk-forward evidence. It is presented as an
independent app section branded exactly **GTP56Sol analysis**. The existing
Analyze and KimiK3 sections, routes, pages, and services remain untouched.

## Decisions

- **Historical nearest-pattern probabilities**, no ML model and no new external
  data provider. The engine uses only persisted daily OHLCV candles and the
  existing technical-analysis indicator math.
- **Three fixed horizons**: `1d` is one forward trading-session bar, `1w` is
  five bars, and `1m` is 21 bars. UI copy says trading-session bars
  (not calendar days) because weekends and market closures create gaps for
  non-crypto assets.
- **Strictly causal evidence**: snapshots use only information available at
  their timestamp. Labels use later prices only after feature construction,
  normalization uses historical candidates only, and walk-forward validation
  uses expanding prior data only.
- **Independent integration**: the backend has a dedicated REST route, MCP
  tool, completed-candle loader, lazy deep-history path, peer selection, and
  cache. The frontend adds only dedicated routes/page/card, navigation entry,
  Trade-page link, and translations. It does not modify or replace `/analysis`, `/kimi-analysis`,
  `AnalyzePage`, or `KimiAnalysisPage`.

## Components

### Forecast engine — `backend/app/services/gtp56sol_analysis.py`

- Pure functions consume oldest-first daily candles in API shape:
  `[timestamp_ms, open, high, low, close, volume]`.
- One O(n) feature-matrix pipeline is the sole definition for current,
  candidate, and walk-forward snapshots. It reuses public indicator primitives
  from `analysis` and applies the same strategy-vote semantics everywhere.
  Features include five strategy votes, normalized RSI, MACD histogram divided
  by price, Bollinger position, ATR percentage, support and resistance
  distances, 1/5/20-bar returns, 20-bar realized volatility, and recent/average
  volume ratio. Missing optional values are represented safely and never cause
  a forecast failure.
- A candidate at index `i` is eligible only when its complete forward horizon
  is known. Its label is based on forward close return and a neutral band:
  `max(0.5%, 0.5 * ATR_pct * sqrt(horizon_bars))`. Returns above the band are
  Up, below its negative are Down, and inside it are Sideways.
- Normalization is fitted from historical candidates only: each feature uses
  its median center and `MAD * 1.4826` robust scale with a safe positive floor.
  When MAD degenerates at the floor despite multiple observations, the scale
  falls back to root-mean-square deviation around the median before flooring;
  this keeps discrete strategy-vote dimensions numerically useful.
  Distance compares dimensions present in both snapshots, normalizes the
  shared squared differences by shared count, and adds an explicit penalty for
  unshared dimensions. Candidates with fewer than eight shared dimensions are
  rejected. Missing values are therefore not median-imputed into artificial
  matches and cannot inflate similarity.
- At least 30 candidates must remain after presence/overlap filtering. The
  nearest 100 at most are selected with deterministic inverse-distance
  weighting. A documented 3% total additive smoothing mass (1% per class) is
  applied after weighting, so an `ok` forecast never presents any outcome as
  exactly impossible or certain. Shares sum to exactly one before
  decimal-string serialization.
- Neighbor outcome windows are interval-deduplicated per source to produce an
  `effective_sample_count`. `sample_count` reports selected neighbors and
  `candidate_pool_size` reports all eligible pool entries. Confidence gates use
  effective independent evidence, not merely the raw/capped neighbor count.
- Direction is `bullish` when Up wins, `bearish` when Down wins, and `neutral`
  when Sideways wins or the Up/Down probability lead is less than `0.10`.
- Confidence is `low`, `medium`, or `high`, conservatively combining raw and
  effective sample counts, average neighbor similarity, probability
  separation, walk-forward directional accuracy, effective validation folds,
  and meaningful improvement over the expanding-prior majority-class
  baseline. Weak, overlapping, sparse, or baseline-level evidence stays low.
  Forecasts using an asset-class fallback pool are capped at Medium because
  correlated peer histories are not equivalent to independent asset evidence.
- The payload contains status, horizon, source scope, probabilities, direction,
  confidence, three structured/i18n-ready drivers, raw/effective sample counts,
  candidate pool size, average similarity, validation and majority-baseline
  metrics, and truthful setup/evidence period boundaries. `period_start` and
  `period_end` always describe the primary asset input; `evidence_period_*`
  describes the candidate pool. All JSON decimals
  use the existing compact 10-significant-digit convention; counts are
  integers.
- `forecast(candles, horizon, fallback_candles_by_market=None)` normally uses
  asset-specific history. If it has fewer than 30 eligible candidates, an
  optional caller-provided same-asset-class markets are pooled with the
  available asset candidates and `source_scope` is `asset_class`. If the
  combined pool does not reach 30, status is `insufficient_history` and no
  probabilities are fabricated.
- Candidate pools defensively retain one entry per
  `(source, feature timestamp, outcome timestamp)`. The caller must not pass
  the primary asset again under a peer market key because the pure engine does
  not receive the primary market identifier.
- Malformed, non-finite, non-positive OHLC, negative-volume, duplicate, or
  out-of-order stored bars are skipped without reordering the remaining
  chronology. Too little usable history degrades to `insufficient_history`
  rather than raising through the route.

### Walk-forward validation

- Validation evaluates historical setups in chronological order.
- Every evaluated setup is compared only with candidates whose feature
  timestamp and complete outcome precede that setup.
- Each counted fold requires at least 100 prior candidates whose complete
  outcome windows end before the target setup. It fits normalization from that
  expanding prior pool and applies the same nearest-neighbor weighting and
  direction rule as the live forecast.
- The response exposes raw/effective evaluated counts, directional accuracy,
  expanding-prior majority-class baseline accuracy, and validation period.
  Evaluation is deterministically capped at 60 evenly spaced folds, always
  including the newest eligible endpoint, to keep runtime reasonable for
  roughly 2,000 daily candles.

### Backend API + MCP

- `GET /markets/{market}/gtp56sol-analysis?horizon=1d|1w|1m` uses the same
  authenticated-user dependency as the other market endpoints. Unknown
  markets return 404 and unsupported horizons return 400. The response is
  `{market, asset_class, generated_at, ...forecast}`.
- Inputs are persisted `MarketCandle` daily rows strictly before the current
  UTC day. The currently forming daily candle is never forecast evidence.
- The engine exposes a lightweight candidate-sufficiency helper. Same-asset-
  class peers are loaded only when the primary cannot meet the selected
  horizon's minimum candidate count. Fallback excludes the primary key and
  every other asset class. Peers are ordered by completed stored-row count
  descending and then market key ascending, capped at 8, and never lazily
  fetched.
- Forecasts are cached for one hour by market, horizon, the primary
  `(first_timestamp, last_timestamp, count)` signature, `ENGINE_VERSION`, and,
  when fallback is used, sorted peer
  `(market, first_timestamp, last_timestamp, count)` signatures. Newer or
  older primary rows, peer-history changes, and engine-version changes
  invalidate naturally. Each full cache key has a loop-safe in-flight async
  lock to prevent duplicate forecasts. Cached values are deep-copied on
  storage and return, and remain public analysis rather than user-specific.
- Cache planning is summary-first: the primary identity and initial
  sufficiency decision use an aggregate summary without hydrating rows. When
  fallback may be needed, all eligible peer min/max/count values come from one
  grouped query before cache lookup. Primary and selected peer arrays are
  hydrated only after a cache miss and inside the in-flight lock; exact
  sanitized sufficiency and signatures are rechecked before computation.
- The pure CPU-heavy engine call receives only detached immutable candle
  tuples and runs through Starlette's threadpool; no SQLAlchemy session crosses
  the worker-thread boundary. A loop-safe semaphore permits at most two
  simultaneous CPU forecasts per process.
- The requested primary market has a lazy five-year target. A per-market async
  lock prevents duplicate work. Attempt outcomes and wall-clock retry times
  are persisted in `AppSetting`: failures retry after about one hour,
  successful expanding fetches after 24 hours, and target-complete or
  no-older-row fetches after 30 days. Completed-history checks use aggregate
  first/last/count queries rather than loading rows.
  Crypto uses at most two 1,000-row Bitvavo pages; non-crypto uses one bounded
  Twelve Data daily time-series request through the existing API-key and EUR
  conversion path. Lazy deep-history calls and the global Kimi candle
  harvester share the same async guard with a 7.5-second minimum gap (at most
  eight/minute across both paths). Provider rows are validated before upsert;
  empty or fully invalid responses are failures with the short one-hour
  cooldown. Only a non-empty valid response that reaches the target or adds no
  older row receives the long completion cooldown. Failures roll back and the
  route forecasts from existing stored rows.
- Kimi's 400-bar all-market harvest cadence and provider requests are
  unchanged. Its stored-candle list and track-record paths explicitly load
  exactly the latest 400 rows, preserving Kimi output semantics and runtime
  after deeper GTP rows are retained.
- Candle retention is 2,000 days so successful per-market deep backfills are
  not immediately pruned. This can add roughly 1,400 rows per individually
  requested market versus the shared 400-bar baseline; it does not backfill
  every catalog market.
- The read-only `get_gtp56sol_analysis(market, horizon="1w")` MCP tool reuses
  the REST handler and its DB session/error mapping. Its contract documents
  probabilities/evidence, 1/5/21 trading-session-bar horizons, same-class
  fallback scope, and the educational simulation disclaimer.
- Existing `/analysis` and `/kimi-analysis` routes and their MCP tools remain
  unchanged.

### Frontend

- Dedicated routes `/gtp56sol-analysis` and
  `/gtp56sol-analysis/:market` are rendered by an independent page component.
  The first route is a searchable, asset-class-filtered picker and does not
  fetch forecasts in bulk.
- The market page requests the three horizons independently and shows
  Next session (1 bar), Next week (5 sessions), and Next month (21 sessions).
  One failed horizon does not hide successful cards. Each card shows
  Up/Sideways/Down probabilities, confidence, structured drivers,
  raw/effective sample and similarity evidence, model and majority-baseline
  walk-forward accuracy, source-scope disclosure, data/generated periods,
  accessible expandable methodology, and an educational paper-money
  simulation disclaimer. Largest-remainder display rounding keeps the three
  visible integer percentages at exactly 100%.
- Forecasts refresh every five minutes and use a dedicated 60-second request
  timeout for cold-cache computation without changing the global API timeout.
  A failed refresh retains the previous result with an inline warning. An
  initial per-horizon error has a Retry action that requests only that horizon.
  Loading, insufficient history, malformed/unknown server enum values,
  invalid or missing probability sets, stale/unavailable responses, fallback
  scope, and zero validation samples have guarded plain-language states.
- Largest-remainder rounding rejects missing, negative, non-finite, zero-total,
  or non-normalized probability sets instead of fabricating a distribution.
  Runtime status, direction, confidence, source, driver, and outcome values
  map through explicit allowlists, so backend drift cannot expose i18n keys.
- All user-facing strings use a separate `gtp56solAnalysis.*` i18n namespace
  in English and Dutch. Desktop/mobile navigation and Trade provide small
  entry links. Existing Analyze and KimiK3 behavior remains unchanged.

## Testing

- `backend/test_gtp56sol_analysis.py` is a deterministic standalone check
  script covering horizon validation, causal snapshots and as-of forecasts,
  volatility-aware labels, probability normalization, determinism,
  trend-shaped synthetic data, exact 29/30 pool floor and nearest-100 cap,
  fallback pooling/scope, robust outlier normalization, presence-aware
  missingness, independent effective evidence, every confidence gate,
  prior-only walk-forward validation and endpoint sampling, malformed/flat
  candle safety, and finite JSON output.
- `backend/test_analysis.py` remains unchanged and must continue to pass.
- `backend/test_gtp56sol_integration.py` is an offline standalone check for
  completed-day filtering and aggregate summaries; deterministic conditional
  same-class peers with primary exclusion; primary/peer/version cache
  invalidation, summary-first cache hits without row hydration, copied payloads
  and stampede locks; awaited/bounded threadpool execution; a real-engine
  in-memory route; Kimi's exact latest-400 isolation; persisted
  failure/success/completion cooldowns including invalid payload behavior;
  shared lazy/harvester Twelve Data spacing;
  same-market rollback safety; route errors; and MCP registration/wrapping.
- `frontend/src/pages/GTP56SolAnalysisPage.test.tsx` runs with the focused
  Vitest and React Testing Library setup. It covers exact-100
  largest-remainder rounding (including tricky decimals), direction and
  confidence language, probabilities and evidence, insufficient history,
  fallback and accessible methodology disclosure, isolated partial failures,
  retained forecasts after refresh errors, per-horizon Retry, the 60-second
  forecast timeout, malformed server values and probabilities, picker
  loading/empty/non-prefetch behavior, and all three horizon endpoint selections.

## Constraints and notes

- Backend route, MCP, persistence, bounded fallback, cache, lazy deep-history,
  and frontend integration are implemented.
- No new external source, package, model training, or opaque score is added.
- Results describe how similar historical setups behaved; they are educational
  probabilities, not guaranteed predictions or real-world financial advice.
