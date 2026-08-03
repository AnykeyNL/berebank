"""Opus calibration: learn feature weights from BereBank's own price history.

The other analysis engines weight their signals by hand. Opus measures instead:
for every feature it computes the daily cross-sectional **information
coefficient** (IC) — the rank correlation between the feature's peer-group
z-score today and the return realized over the next 1, 5 or 21 trading bars —
and turns the average IC into a weight. Features whose IC is statistically
indistinguishable from noise get weight zero automatically, so the engine
cannot be talked into believing a signal that never worked.

Everything is computed in one strictly forward pass over the stored daily
panel:

- Running IC statistics use only days *before* the day being scored, so the
  composite scores collected along the way are genuinely out-of-sample. Their
  IC and hit rate become the engine's reported walk-forward diagnostics.
- The score-to-return map is a set of fixed bins over the normalized composite,
  filled with the average forward return observed in each bin and then
  monotonically smoothed. That is what converts an abstract score into an
  `expected_return_pct` comparable to trading fees.
- Weights are estimated per peer group, per horizon and per market regime
  (peer index above or below its 50-day mean), with regime weights shrunk
  halfway toward the pooled estimate to keep small buckets sane.

Pure computation: the caller supplies the panel and persists the result.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from operator import mul

from .opus_features import (
    FEATURE_KEYS,
    MIN_CROSS_SECTION,
    PEER_GROUPS,
    MarketSeries,
    cross_section,
    group_members_by_day,
)

ENGINE_VERSION = "opus-1"

# Horizon label -> forward trading bars. 1 day to 4 weeks, the contest window.
HORIZONS: dict[str, int] = {"1d": 1, "1w": 5, "4w": 21}
DEFAULT_HORIZON = "1w"

REGIMES = ("all", "up", "down")

# Prior weights: the sign and rough importance each feature is expected to
# have from published cross-sectional research. Used only where the data
# cannot support a learned weight yet (young installs, thin peer groups, and
# the single-market walk-forward track record).
PRIOR_WEIGHTS: dict[str, float] = {
    "mom_21": 0.14,
    "mom_63": 0.09,
    "accel": 0.04,
    "rev_5": 0.11,
    "rev_1": 0.05,
    "ma_dist": 0.09,
    "adx_dir": 0.07,
    "rsi_dev": -0.05,
    "bb_pos": -0.04,
    "range_pos": 0.05,
    "vol_ratio": -0.03,
    "vol_level": -0.06,
    "dd_63": 0.04,
    "vol_z": 0.03,
    "turnover": 0.02,
    "beta_mkt": 0.0,
    "corr_mkt": 0.0,
    "resid_mom": 0.09,
    # Macro sensitivities have no defensible prior sign — whether being
    # rate-sensitive helps depends entirely on the regime — so they start at
    # zero and only earn weight if the data says so.
    "beta_vix": 0.0,
    "beta_rate": 0.0,
    "beta_fng": 0.0,
    "beta_stable": 0.0,
    "funding": -0.05,
}

# Statistical gates for turning an IC into a weight.
MIN_IC_DAYS = 30          # daily ICs needed before a feature may carry weight
MIN_EFFECTIVE_DAYS = 12   # independent (non-overlapping) observations needed
MIN_ABS_T = 1.0           # |t| below this is treated as noise -> weight 0
STRONG_ABS_T = 2.0        # conventional two-standard-error significance
SHRINK_T0 = 2.0           # weight = mean_ic * |t| / (|t| + SHRINK_T0)
PRIOR_BLEND = 0.2         # share of the prior kept in a learned weight vector
REGIME_SHRINK = 0.5       # regime weights pulled halfway to the pooled vector
MIN_LEARNED_FEATURES = 3  # below this the prior vector is used instead
# One standard error admits a feature to the vector, but the vector as a whole
# only counts as learned once several features clear two standard errors. With
# 23 candidate features, one-standard-error gating alone would let a purely
# random panel produce confident-looking weights.
MIN_STRONG_FEATURES = 2

# Fixed bins over the normalized composite score for the score -> return map.
BIN_EDGES: tuple[float, ...] = (-1.5, -1.0, -0.6, -0.3, 0.0, 0.3, 0.6, 1.0, 1.5)
MIN_BIN_SAMPLES = 30

# Days of running history before walk-forward samples are recorded.
WALK_FORWARD_WARMUP = 60

# Regime classification: peer index level versus its own moving average.
REGIME_MA_DAYS = 50


def clip_return(value: float, bars: int) -> float:
    """Bound a forward return so one lottery ticket cannot set the weights."""
    limit = 0.3 * math.sqrt(bars)
    return max(-limit, min(limit, value))


def _rank_scores(values: list[float]) -> list[float]:
    """Average ranks mapped to [-0.5, 0.5], for Spearman-style correlation."""
    n = len(values)
    if n < 2:
        return [0.0] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return [rank / (n - 1) - 0.5 for rank in ranks]


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation using C-level reductions (hot path in calibration).

    Both inputs are rank transforms here, so this is a Spearman correlation.
    """
    n = len(xs)
    if n < MIN_CROSS_SECTION:
        return None
    sum_x = sum(xs)
    sum_y = sum(ys)
    covariance = sum(map(mul, xs, ys)) - sum_x * sum_y / n
    variance_x = sum(map(mul, xs, xs)) - sum_x * sum_x / n
    variance_y = sum(map(mul, ys, ys)) - sum_y * sum_y / n
    if variance_x <= 0 or variance_y <= 0:
        return None
    return covariance / math.sqrt(variance_x * variance_y)


class _Accumulator:
    """Running mean and overlap-corrected significance of a series of ICs.

    Daily observations of an ``overlap``-day forward return are largely the same
    observation seen repeatedly, so the effective sample size is ``count /
    overlap``. Without that correction a 4-week signal would look about five
    times more significant than it is.
    """

    __slots__ = ("count", "total", "total_sq", "overlap")

    def __init__(self, overlap: int = 1) -> None:
        self.count = 0
        self.total = 0.0
        self.total_sq = 0.0
        self.overlap = max(1, overlap)

    def add(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.total_sq += value * value

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    @property
    def effective_count(self) -> float:
        return self.count / self.overlap

    def t_stat(self) -> float:
        effective = self.effective_count
        if self.count < 3 or effective < 2:
            return 0.0
        mean = self.mean
        variance = max(self.total_sq / self.count - mean * mean, 0.0)
        if variance <= 0:
            return 0.0
        standard_error = math.sqrt(variance / (effective - 1))
        if standard_error <= 0:
            return 0.0
        return mean / standard_error

    def shrink_factor(self) -> float:
        """Confidence-weighted shrinkage of the mean toward zero."""
        t_stat = abs(self.t_stat())
        if t_stat < MIN_ABS_T:
            return 0.0
        return t_stat / (t_stat + SHRINK_T0)


def _normalized_priors() -> dict[str, float]:
    total = sum(abs(w) for w in PRIOR_WEIGHTS.values()) or 1.0
    return {key: value / total for key, value in PRIOR_WEIGHTS.items()}


def weights_from_ic(stats: dict[str, _Accumulator]) -> tuple[dict[str, float], bool]:
    """Turn per-feature IC statistics into a normalized weight vector.

    Returns the weights and whether they are learned (``True``) or the prior
    fallback (``False``).
    """
    learned: dict[str, float] = {}
    strong = 0
    for feature in FEATURE_KEYS:
        accumulator = stats.get(feature)
        if accumulator is None:
            continue
        if accumulator.count < MIN_IC_DAYS or accumulator.effective_count < MIN_EFFECTIVE_DAYS:
            continue
        weight = accumulator.mean * accumulator.shrink_factor()
        if weight:
            learned[feature] = weight
            if abs(accumulator.t_stat()) >= STRONG_ABS_T:
                strong += 1

    priors = _normalized_priors()
    if len(learned) < MIN_LEARNED_FEATURES or strong < MIN_STRONG_FEATURES:
        return dict(priors), False

    total = sum(abs(w) for w in learned.values()) or 1.0
    blended: dict[str, float] = {}
    for feature in FEATURE_KEYS:
        value = (1.0 - PRIOR_BLEND) * (learned.get(feature, 0.0) / total)
        value += PRIOR_BLEND * priors.get(feature, 0.0)
        if value:
            blended[feature] = value
    total = sum(abs(w) for w in blended.values()) or 1.0
    return {feature: value / total for feature, value in blended.items()}, True


def composite_score(z_scores: dict[str, float], weights: dict[str, float]) -> tuple[float, float]:
    """Weighted sum of available feature z-scores plus the weight actually used.

    Missing features are skipped and the sum is rescaled by the share of weight
    present, so a market with partial data is neither penalized nor flattered.
    """
    total = 0.0
    used = 0.0
    for feature, weight in weights.items():
        z = z_scores.get(feature)
        if z is None or not weight:
            continue
        total += weight * z
        used += abs(weight)
    if used <= 0:
        return 0.0, 0.0
    return total / used, used


def _monotone(means: list[float | None], counts: list[int]) -> list[float | None]:
    """Pool-adjacent-violators smoothing so returns rise with the score."""
    points = [(i, m, counts[i]) for i, m in enumerate(means) if m is not None and counts[i]]
    if len(points) < 2:
        return means
    blocks = [([i], value, float(count)) for i, value, count in points]
    changed = True
    while changed:
        changed = False
        merged: list[tuple[list[int], float, float]] = []
        for block in blocks:
            if merged and merged[-1][1] > block[1]:
                indexes, value, weight = merged.pop()
                total_weight = weight + block[2]
                merged.append((
                    indexes + block[0],
                    (value * weight + block[1] * block[2]) / total_weight,
                    total_weight,
                ))
                changed = True
            else:
                merged.append(block)
        blocks = merged
    out: list[float | None] = list(means)
    for indexes, value, _weight in blocks:
        for index in indexes:
            out[index] = value
    return out


class _BinTable:
    """Average forward return per composite-score bin."""

    __slots__ = ("counts", "totals")

    def __init__(self) -> None:
        size = len(BIN_EDGES) + 1
        self.counts = [0] * size
        self.totals = [0.0] * size

    def add(self, composite: float, forward: float) -> None:
        index = bin_index(composite)
        self.counts[index] += 1
        self.totals[index] += forward

    def payload(self) -> list[dict]:
        means: list[float | None] = [
            (self.totals[i] / self.counts[i]) if self.counts[i] >= MIN_BIN_SAMPLES else None
            for i in range(len(self.counts))
        ]
        smoothed = _monotone(means, self.counts)
        return [
            {"bin": i, "count": self.counts[i], "mean_return_pct": None if value is None else value * 100}
            for i, value in enumerate(smoothed)
        ]


def bin_index(composite: float) -> int:
    for index, edge in enumerate(BIN_EDGES):
        if composite < edge:
            return index
    return len(BIN_EDGES)


def bin_center(index: int) -> float:
    if index == 0:
        return BIN_EDGES[0] - 0.5
    if index == len(BIN_EDGES):
        return BIN_EDGES[-1] + 0.5
    return (BIN_EDGES[index - 1] + BIN_EDGES[index]) / 2.0


def regime_by_day(index_returns: dict[str, float]) -> dict[str, str]:
    """Classify each day as ``up`` or ``down`` from the peer index trend.

    The index level is the cumulative equal-weight return of the peer group, so
    this regime label needs no external provider and exists for every day of
    stored history.
    """
    days = sorted(index_returns)
    level = 0.0
    levels: list[float] = []
    out: dict[str, str] = {}
    for day in days:
        level += index_returns[day]
        levels.append(level)
        window = levels[-REGIME_MA_DAYS:]
        average = sum(window) / len(window)
        out[day] = "up" if level >= average else "down"
    return out


def calibrate_group(
    panel: dict[str, MarketSeries],
    group: str,
    regimes: dict[str, str],
) -> dict[tuple[str, str], dict]:
    """Calibrate one peer group; returns ``{(horizon, regime): payload}``."""
    members_by_day = group_members_by_day(panel, group)
    days = sorted(members_by_day)
    if not days:
        return {}

    ic_stats: dict[tuple[str, str], dict[str, _Accumulator]] = {
        (horizon, regime): {
            feature: _Accumulator(HORIZONS[horizon]) for feature in FEATURE_KEYS
        }
        for horizon in HORIZONS
        for regime in REGIMES
    }
    composite_ic: dict[tuple[str, str], _Accumulator] = {
        (horizon, regime): _Accumulator(HORIZONS[horizon])
        for horizon in HORIZONS
        for regime in REGIMES
    }
    bins: dict[tuple[str, str], _BinTable] = {key: _BinTable() for key in ic_stats}
    market_return: dict[tuple[str, str], _Accumulator] = {
        (horizon, regime): _Accumulator(HORIZONS[horizon])
        for horizon in HORIZONS
        for regime in REGIMES
    }
    hits: dict[tuple[str, str], list[int]] = {key: [0, 0] for key in ic_stats}
    composite_spread: dict[tuple[str, str], _Accumulator] = {
        key: _Accumulator() for key in ic_stats
    }
    day_count = {key: 0 for key in ic_stats}

    for day in days:
        members = members_by_day[day]
        if len(members) < MIN_CROSS_SECTION:
            continue
        regime = regimes.get(day, "up")
        z_by_market = cross_section(panel, members)

        for horizon, bars in HORIZONS.items():
            forwards: list[float] = []
            markets: list[str] = []
            for market, index in members:
                value = panel[market].forward_return(index, bars)
                if value is None:
                    continue
                forwards.append(clip_return(value, bars))
                markets.append(market)
            if len(markets) < MIN_CROSS_SECTION:
                continue
            mean_forward = sum(forwards) / len(forwards)
            relative = [value - mean_forward for value in forwards]
            forward_ranks = _rank_scores(relative)
            z_rows = [z_by_market.get(market) or {} for market in markets]

            feature_ic: dict[str, float] = {}
            for feature in FEATURE_KEYS:
                feature_values: list[float] = []
                feature_forwards: list[float] = []
                for row, forward_rank in zip(z_rows, forward_ranks):
                    z = row.get(feature)
                    if z is None:
                        continue
                    feature_values.append(z)
                    feature_forwards.append(forward_rank)
                correlation = _correlation(feature_values, feature_forwards)
                if correlation is not None:
                    feature_ic[feature] = correlation

            for regime_key in ("all", regime):
                key = (horizon, regime_key)
                stats = ic_stats[key]
                # Walk-forward: score today with weights learned before today.
                weights, learned = weights_from_ic(stats)
                if not learned:
                    weights, _ = weights_from_ic(ic_stats[(horizon, "all")])
                spread = composite_spread[key]
                scale = (
                    math.sqrt(max(spread.total_sq / spread.count, 1e-12))
                    if spread.count
                    else 0.0
                )
                ready = day_count[key] >= WALK_FORWARD_WARMUP and scale > 0

                composites: list[float] = []
                for row, forward in zip(z_rows, relative):
                    raw, used = composite_score(row, weights)
                    composites.append(raw)
                    spread.add(raw)
                    if not ready or not used:
                        continue
                    normalized = raw / scale
                    bins[key].add(normalized, forward)
                    if abs(normalized) >= 0.5:
                        hits[key][1] += 1
                        if (normalized > 0) == (forward > 0):
                            hits[key][0] += 1

                for feature, correlation in feature_ic.items():
                    stats[feature].add(correlation)

                if ready:
                    correlation = _correlation(composites, forward_ranks)
                    if correlation is not None:
                        composite_ic[key].add(correlation)
                market_return[key].add(mean_forward)
                day_count[key] += 1

    out: dict[tuple[str, str], dict] = {}
    for horizon, bars in HORIZONS.items():
        pooled_weights, pooled_learned = weights_from_ic(ic_stats[(horizon, "all")])
        for regime in REGIMES:
            key = (horizon, regime)
            if day_count[key] < MIN_IC_DAYS or day_count[key] / bars < MIN_EFFECTIVE_DAYS:
                continue
            weights, learned = weights_from_ic(ic_stats[key])
            if regime != "all" and learned and pooled_learned:
                merged = {}
                for feature in FEATURE_KEYS:
                    value = (1 - REGIME_SHRINK) * weights.get(feature, 0.0)
                    value += REGIME_SHRINK * pooled_weights.get(feature, 0.0)
                    if value:
                        merged[feature] = value
                total = sum(abs(w) for w in merged.values()) or 1.0
                weights = {f: w / total for f, w in merged.items()}
            elif not learned:
                weights = pooled_weights
                learned = pooled_learned

            spread = composite_spread[key]
            scale = (
                math.sqrt(max(spread.total_sq / spread.count, 1e-12))
                if spread.count
                else 1.0
            )
            walk_forward_ic = composite_ic[key].mean
            walk_forward_days = composite_ic[key].count
            # A composite that did not predict its own out-of-sample direction
            # must not be turned into an expected return. Its weights still
            # rank markets, but the euro figure is withheld.
            reliable = (
                walk_forward_ic > 0
                and walk_forward_days >= MIN_IC_DAYS
                and composite_ic[key].effective_count >= MIN_EFFECTIVE_DAYS
            )
            hit, total_hits = hits[key]
            returns = market_return[key]
            variance = max(returns.total_sq / returns.count - returns.mean ** 2, 0.0) if returns.count else 0.0
            # The peer group's average forward return is a weak estimate of
            # future drift, so shrink it by its own significance rather than
            # projecting the sample mean forward at face value.
            drift_pct = returns.mean * returns.shrink_factor() * 100
            out[key] = {
                "engine_version": ENGINE_VERSION,
                "peer_group": group,
                "horizon": horizon,
                "regime": regime,
                "weights": weights,
                "weights_learned": learned,
                "composite_scale": scale,
                "ic": {
                    feature: {
                        "mean": ic_stats[key][feature].mean,
                        "t": ic_stats[key][feature].t_stat(),
                        "days": ic_stats[key][feature].count,
                    }
                    for feature in FEATURE_KEYS
                    if ic_stats[key][feature].count
                },
                "bins": bins[key].payload(),
                "market_return": {
                    "mean_pct": drift_pct,
                    "sample_mean_pct": returns.mean * 100,
                    "std_pct": math.sqrt(variance) * 100,
                    "days": returns.count,
                },
                "reliable": reliable,
                "walk_forward": {
                    "ic": walk_forward_ic,
                    "ic_t": composite_ic[key].t_stat(),
                    "ic_days": walk_forward_days,
                    "hit_rate_pct": (hit / total_hits * 100) if total_hits else None,
                    "samples": total_hits,
                },
                "days": day_count[key],
                "from": days[0],
                "to": days[-1],
                "markets": len({market for market, _ in members_by_day[days[-1]]}),
            }
    return out


def calibrate(
    panel: dict[str, MarketSeries],
    index_returns: dict[str, dict[str, float]],
) -> dict[tuple[str, str, str], dict]:
    """Calibrate every peer group; keys are ``(group, horizon, regime)``."""
    out: dict[tuple[str, str, str], dict] = {}
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for group in PEER_GROUPS:
        regimes = regime_by_day(index_returns.get(group) or {})
        for (horizon, regime), payload in calibrate_group(panel, group, regimes).items():
            payload["calibrated_at"] = generated_at
            out[(group, horizon, regime)] = payload
    return out


def pick_payload(
    calibrations: dict[tuple[str, str, str], dict],
    group: str,
    horizon: str,
    regime: str,
) -> dict | None:
    """Best calibration available for a market: regime-specific if it held up
    out of sample, otherwise the pooled one, otherwise whatever exists."""
    candidates = [
        calibrations.get((group, horizon, regime)),
        calibrations.get((group, horizon, "all")),
    ]
    for candidate in candidates:
        if candidate and candidate.get("reliable"):
            return candidate
    return next((candidate for candidate in candidates if candidate), None)


def current_regime(index_returns: dict[str, float]) -> str:
    """Regime label for the most recent day of a peer index."""
    labels = regime_by_day(index_returns)
    if not labels:
        return "all"
    return labels[sorted(labels)[-1]]


def expected_alpha_pct(payload: dict, composite: float) -> float | None:
    """Expected peer-relative return (percent) for a composite score.

    Reads the calibrated bin map and interpolates between bin centers, so the
    mapping is monotone and stays flat outside the calibrated range instead of
    extrapolating a fantasy.
    """
    if not payload.get("reliable"):
        return None
    scale = payload.get("composite_scale") or 1.0
    normalized = composite / scale if scale else 0.0
    points: list[tuple[float, float]] = []
    for entry in payload.get("bins") or []:
        value = entry.get("mean_return_pct")
        if value is None:
            continue
        points.append((bin_center(int(entry["bin"])), float(value)))
    if not points:
        # No bin has enough samples yet. A linear fallback is only defensible
        # while the composite actually predicted the right direction: a negative
        # walk-forward IC would otherwise be inverted into fake edge.
        walk_forward_ic = (payload.get("walk_forward") or {}).get("ic") or 0.0
        deviation = (payload.get("market_return") or {}).get("std_pct")
        if walk_forward_ic <= 0 or not deviation:
            return None
        return walk_forward_ic * float(deviation) * normalized
    points.sort()
    if normalized <= points[0][0]:
        return points[0][1]
    if normalized >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= normalized <= x1:
            if x1 == x0:
                return y1
            ratio = (normalized - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)
    return points[-1][1]
