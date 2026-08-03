"""Opus feature engineering: per-market indicators plus cross-sectional ranking.

Two stages, both pure functions over candle lists (no database, no request
state):

1. **Per-market extraction** — one causal pass over a market's daily bars
   produces ~17 features per bar: volatility-normalized momentum over two
   horizons, short-term reversal, distance to the 50-day mean in ATR units,
   signed ADX, RSI/Bollinger/range position, volatility expansion and level,
   drawdown, volume surge, turnover, and (once relative features are added)
   beta, correlation and residual momentum versus the peer-group index.
2. **Cross-sectional scoring** — on each day every feature is rank-transformed
   *within its peer group* and scaled to roughly unit standard deviation. This
   is what separates Opus from the other engines: a score answers "is this the
   best of today's alternatives", not "does this cross an absolute threshold",
   which removes market-wide drift and makes one number comparable across
   ~560 very different instruments.

Peer groups keep the comparison meaningful: ``crypto``, ``stock``, and
``other`` (funds plus commodities, which are too few to rank separately).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import analysis

# Bars of history before any feature is defined (longest lookback + buffer).
WARMUP_BARS = 70

# Fewest markets a peer group needs on a day before ranking says anything.
MIN_CROSS_SECTION = 5

# Uniform ranks in [-0.5, 0.5] have standard deviation 1/sqrt(12).
_RANK_SCALE = math.sqrt(12.0)

PEER_GROUPS = ("crypto", "stock", "other")

# Ordered so weight vectors and UI tables always read the same way.
FEATURE_KEYS = (
    "mom_21",
    "mom_63",
    "accel",
    "rev_5",
    "rev_1",
    "ma_dist",
    "adx_dir",
    "rsi_dev",
    "bb_pos",
    "range_pos",
    "vol_ratio",
    "vol_level",
    "dd_63",
    "vol_z",
    "turnover",
    "beta_mkt",
    "corr_mkt",
    "resid_mom",
    "beta_vix",
    "beta_rate",
    "beta_fng",
    "beta_stable",
    "funding",
)

# Macro feature -> the change series it is regressed against. These turn a
# day-constant macro reading (useless in a cross-section) into a per-market
# sensitivity, which is exactly what differs between assets on the same day.
MACRO_BETAS = {
    "beta_vix": "vix",
    "beta_rate": "rate",
    "beta_fng": "fng",
    "beta_stable": "stable",
}

# Bars of overlap a regression needs before its estimate is used.
MIN_REGRESSION_PAIRS = 20


def peer_group(asset_class: str) -> str:
    """Map an asset class to its cross-sectional peer group."""
    if asset_class == "crypto":
        return "crypto"
    if asset_class == "stock":
        return "stock"
    return "other"


@dataclass
class MarketSeries:
    """Per-bar features and prices for one market, oldest bar first."""

    market: str
    asset_class: str
    days: list[str]
    closes: list[float]
    returns: list[float | None]
    features: dict[str, list[float | None]]
    index_by_day: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.index_by_day:
            self.index_by_day = {day: i for i, day in enumerate(self.days)}

    @property
    def group(self) -> str:
        return peer_group(self.asset_class)

    def feature_at(self, index: int) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, values in self.features.items():
            value = values[index]
            if value is not None:
                out[key] = value
        return out

    def forward_return(self, index: int, bars: int) -> float | None:
        target = index + bars
        if target >= len(self.closes):
            return None
        base = self.closes[index]
        if base <= 0:
            return None
        return self.closes[target] / base - 1.0


# ---- small numeric helpers ----

def _stdev(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return math.sqrt(var)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _safe_log(value: float) -> float | None:
    if value <= 0:
        return None
    return math.log(value)


def _clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _day_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date().isoformat()


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Wilder's ADX with the +DI/-DI lines, aligned to ``closes``."""
    n = len(closes)
    empty: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return empty, list(empty), list(empty)

    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    adx_out: list[float | None] = [None] * n
    plus_out: list[float | None] = [None] * n
    minus_out: list[float | None] = [None] * n

    tr_sum = sum(trs[:period])
    plus_sum = sum(plus_dm[:period])
    minus_sum = sum(minus_dm[:period])
    dx_values: list[float] = []
    for i in range(period, len(trs) + 1):
        if i > period:
            tr_sum = tr_sum - tr_sum / period + trs[i - 1]
            plus_sum = plus_sum - plus_sum / period + plus_dm[i - 1]
            minus_sum = minus_sum - minus_sum / period + minus_dm[i - 1]
        bar = i  # trs[j] describes the move into closes[j + 1]
        if tr_sum <= 0:
            continue
        plus_di = 100.0 * plus_sum / tr_sum
        minus_di = 100.0 * minus_sum / tr_sum
        plus_out[bar] = plus_di
        minus_out[bar] = minus_di
        total = plus_di + minus_di
        if total <= 0:
            continue
        dx_values.append(100.0 * abs(plus_di - minus_di) / total)
        if len(dx_values) == period:
            adx_out[bar] = sum(dx_values) / period
        elif len(dx_values) > period:
            previous = adx_out[bar - 1]
            if previous is not None:
                adx_out[bar] = (previous * (period - 1) + dx_values[-1]) / period
    return adx_out, plus_out, minus_out


# ---- stage 1: per-market features ----

def extract_market_features(
    market: str,
    asset_class: str,
    candles: list[list],
) -> MarketSeries | None:
    """Build the per-bar feature series for one market's daily candles.

    ``candles`` are API-shape ``[timestamp_ms, o, h, l, c, v]`` oldest first.
    Every value at bar ``i`` uses only bars up to and including ``i``.
    """
    if len(candles) <= WARMUP_BARS:
        return None
    try:
        days = [_day_iso(int(c[0])) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
        volumes = [float(c[5]) for c in candles]
    except (IndexError, TypeError, ValueError):
        return None
    n = len(closes)
    if min(closes) <= 0:
        return None

    returns: list[float | None] = [None] * n
    for i in range(1, n):
        if closes[i - 1] > 0:
            ratio = closes[i] / closes[i - 1]
            returns[i] = math.log(ratio) if ratio > 0 else None

    rsi_values = analysis.rsi(closes, 14)
    sma50 = analysis.sma(closes, 50)
    atr_values = analysis.atr(highs, lows, closes, 14)
    mid, upper, _lower = analysis.bollinger(closes, 20, 2.0)
    adx_values, plus_di, minus_di = adx(highs, lows, closes, 14)

    features: dict[str, list[float | None]] = {
        key: [None] * n for key in FEATURE_KEYS
    }

    def vol(index: int, window: int) -> float | None:
        start = index - window + 1
        if start < 1:
            return None
        chunk = [r for r in returns[start : index + 1] if r is not None]
        if len(chunk) < max(3, window - 2):
            return None
        return _stdev(chunk)

    for i in range(WARMUP_BARS, n):
        close = closes[i]
        vol_21 = vol(i, 21)
        vol_63 = vol(i, 63)
        vol_5 = vol(i, 5)

        if vol_21:
            ratio = _safe_log(close / closes[i - 21])
            if ratio is not None:
                features["mom_21"][i] = _clip(ratio / (vol_21 * math.sqrt(21)), 5.0)
            short = _safe_log(close / closes[i - 5])
            if short is not None:
                features["rev_5"][i] = _clip(-short / (vol_21 * math.sqrt(5)), 5.0)
            last = returns[i]
            if last is not None:
                features["rev_1"][i] = _clip(-last / vol_21, 5.0)
        if vol_63:
            long_ratio = _safe_log(close / closes[i - 63])
            if long_ratio is not None:
                features["mom_63"][i] = _clip(long_ratio / (vol_63 * math.sqrt(63)), 5.0)
        mom_21, mom_63 = features["mom_21"][i], features["mom_63"][i]
        if mom_21 is not None and mom_63 is not None:
            features["accel"][i] = mom_21 - mom_63

        mean50, atr14 = sma50[i], atr_values[i]
        if mean50 is not None and atr14 and atr14 > 0:
            features["ma_dist"][i] = _clip((close - mean50) / atr14, 10.0)

        adx_value, di_plus, di_minus = adx_values[i], plus_di[i], minus_di[i]
        if adx_value is not None and di_plus is not None and di_minus is not None:
            sign = 1.0 if di_plus >= di_minus else -1.0
            features["adx_dir"][i] = sign * adx_value / 100.0

        rsi_value = rsi_values[i]
        if rsi_value is not None:
            features["rsi_dev"][i] = (rsi_value - 50.0) / 50.0

        middle, band = mid[i], upper[i]
        if middle is not None and band is not None and band > middle:
            features["bb_pos"][i] = _clip((close - middle) / (band - middle), 4.0)

        window = closes[i - 19 : i + 1]
        low_20, high_20 = min(window), max(window)
        if high_20 > low_20:
            features["range_pos"][i] = (close - low_20) / (high_20 - low_20) * 2.0 - 1.0

        if vol_5 and vol_21:
            expansion = _safe_log(vol_5 / vol_21)
            if expansion is not None:
                features["vol_ratio"][i] = _clip(expansion, 3.0)
        if vol_21:
            features["vol_level"][i] = vol_21

        peak = max(closes[i - 62 : i + 1]) if i >= 62 else max(closes[: i + 1])
        if peak > 0:
            features["dd_63"][i] = close / peak - 1.0

        recent_volume = volumes[i - 4 : i + 1]
        base_volume = volumes[i - 20 : i + 1]
        recent_mean = sum(recent_volume) / len(recent_volume)
        base_mean = sum(base_volume) / len(base_volume)
        if recent_mean > 0 and base_mean > 0:
            surge = _safe_log(recent_mean / base_mean)
            if surge is not None:
                features["vol_z"][i] = _clip(surge, 3.0)

        turnovers = [
            volumes[j] * closes[j]
            for j in range(i - 20, i + 1)
            if volumes[j] > 0
        ]
        if turnovers:
            median_turnover = _safe_log(_median(turnovers))
            if median_turnover is not None:
                features["turnover"][i] = median_turnover

    return MarketSeries(
        market=market,
        asset_class=asset_class,
        days=days,
        closes=closes,
        returns=returns,
        features=features,
    )


def build_panel(
    candles_by_market: dict[str, list[list]],
    asset_class_by_market: dict[str, str],
) -> dict[str, MarketSeries]:
    """Extract features for every market that has enough history."""
    panel: dict[str, MarketSeries] = {}
    for market, candles in candles_by_market.items():
        asset_class = asset_class_by_market.get(market)
        if asset_class is None:
            continue
        series = extract_market_features(market, asset_class, candles)
        if series is not None:
            panel[market] = series
    return panel


# ---- stage 1b: peer-group index and market-relative features ----

def group_index_returns(panel: dict[str, MarketSeries]) -> dict[str, dict[str, float]]:
    """Equal-weight daily log return of each peer group, keyed by day.

    Built from BereBank's own stored candles, so this "market index" has full
    history for every asset class without any external provider.
    """
    sums: dict[str, dict[str, list[float]]] = {group: {} for group in PEER_GROUPS}
    for series in panel.values():
        bucket = sums[series.group]
        for i, day in enumerate(series.days):
            value = series.returns[i]
            if value is None:
                continue
            bucket.setdefault(day, []).append(value)
    out: dict[str, dict[str, float]] = {}
    for group, by_day in sums.items():
        out[group] = {
            day: sum(values) / len(values)
            for day, values in by_day.items()
            if len(values) >= 3
        }
    return out


def _rolling_regression(
    ys: list[float | None],
    xs: list[float | None],
    window: int,
    short_window: int,
) -> list[tuple[float, float, float, float, float, int] | None]:
    """Rolling univariate regression of ``ys`` on ``xs``, one pass.

    Returns per bar ``(beta, correlation, alpha, short_y, short_x, short_n)``
    where the ``short_*`` terms are the sums over the trailing
    ``short_window`` bars, which is all residual momentum needs. Sums are
    updated incrementally, so the whole panel costs one pass instead of one
    regression per bar.
    """
    n = len(ys)
    out: list[tuple[float, float, float, float, float, int] | None] = [None] * n
    sum_x = sum_y = sum_xy = sum_xx = sum_yy = 0.0
    count = 0
    short_x = short_y = 0.0
    short_count = 0

    for i in range(n):
        x, y = xs[i], ys[i]
        if x is not None and y is not None:
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_xx += x * x
            sum_yy += y * y
            count += 1
            short_x += x
            short_y += y
            short_count += 1
        drop = i - window
        if drop >= 0:
            old_x, old_y = xs[drop], ys[drop]
            if old_x is not None and old_y is not None:
                sum_x -= old_x
                sum_y -= old_y
                sum_xy -= old_x * old_y
                sum_xx -= old_x * old_x
                sum_yy -= old_y * old_y
                count -= 1
        drop = i - short_window
        if drop >= 0:
            old_x, old_y = xs[drop], ys[drop]
            if old_x is not None and old_y is not None:
                short_x -= old_x
                short_y -= old_y
                short_count -= 1
        if count < MIN_REGRESSION_PAIRS:
            continue
        covariance = sum_xy - sum_x * sum_y / count
        variance_x = sum_xx - sum_x * sum_x / count
        variance_y = sum_yy - sum_y * sum_y / count
        if variance_x <= 0:
            continue
        beta = covariance / variance_x
        correlation = (
            covariance / math.sqrt(variance_x * variance_y) if variance_y > 0 else 0.0
        )
        alpha = (sum_y - beta * sum_x) / count
        out[i] = (beta, correlation, alpha, short_y, short_x, short_count)
    return out


def add_relative_features(
    panel: dict[str, MarketSeries],
    index_returns: dict[str, dict[str, float]],
    macro_changes: dict[str, dict[str, float]] | None = None,
    *,
    window: int = 63,
    momentum_window: int = 21,
) -> None:
    """Add peer-index and macro regression features to every market.

    Versus the peer index: beta, correlation and residual momentum — the part of
    the recent move the asset class does not explain, which is a cleaner
    short-horizon signal than raw momentum. Beta and correlation additionally
    tell the ranking which candidates are the same bet in different clothes.

    Versus the macro change series: one sensitivity per series (VIX, 10-year
    yield, crypto sentiment, stablecoin supply). A macro reading is identical
    for every market on a given day and therefore says nothing about which one
    to hold; how strongly each market *responds* to it does.
    """
    macro_changes = macro_changes or {}
    for series in panel.values():
        n = len(series.days)
        index_by_day = index_returns.get(series.group) or {}
        if index_by_day:
            bench = [index_by_day.get(day) for day in series.days]
            regression = _rolling_regression(series.returns, bench, window, momentum_window)
            for i in range(WARMUP_BARS, n):
                stats = regression[i]
                if stats is None:
                    continue
                beta, correlation, alpha, short_y, short_x, short_n = stats
                series.features["beta_mkt"][i] = _clip(beta, 5.0)
                series.features["corr_mkt"][i] = _clip(correlation, 1.0)
                vol_level = series.features["vol_level"][i]
                if short_n >= momentum_window // 2 and vol_level:
                    residual = short_y - short_n * alpha - beta * short_x
                    series.features["resid_mom"][i] = _clip(
                        residual / (vol_level * math.sqrt(short_n)), 5.0
                    )

        for feature, key in MACRO_BETAS.items():
            changes = macro_changes.get(key)
            if not changes:
                continue
            bench = [changes.get(day) for day in series.days]
            regression = _rolling_regression(series.returns, bench, window, momentum_window)
            target = series.features[feature]
            for i in range(WARMUP_BARS, n):
                stats = regression[i]
                if stats is not None:
                    target[i] = _clip(stats[0], 20.0)


def attach_series_feature(
    panel: dict[str, MarketSeries],
    feature: str,
    values_by_market: dict[str, dict[str, float]],
) -> None:
    """Write an externally harvested per-market daily series into the panel.

    Used for Coinglass funding, which only accumulates history from the day
    harvesting starts; missing days simply leave the feature undefined and the
    calibration gives it zero weight until there is enough of it.
    """
    for market, series in panel.items():
        by_day = values_by_market.get(market)
        if not by_day:
            continue
        target = series.features.get(feature)
        if target is None:
            continue
        for day, value in by_day.items():
            index = series.index_by_day.get(day)
            if index is not None and index >= WARMUP_BARS:
                target[index] = value


# ---- stage 2: cross-sectional scoring ----

def rank_z_scores(values: dict[str, float]) -> dict[str, float]:
    """Rank-transform ``{market: value}`` to roughly unit-variance z-scores.

    Ranking rather than standardizing makes the score immune to the fat tails
    and occasional bad ticks that raw crypto features are full of.
    """
    n = len(values)
    if n < MIN_CROSS_SECTION:
        return {market: 0.0 for market in values}
    ordered = sorted(values.items(), key=lambda item: item[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        average = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = average
        i = j + 1
    return {
        market: (rank / (n - 1) - 0.5) * _RANK_SCALE
        for market, rank in ranks.items()
    }


def cross_section(
    panel: dict[str, MarketSeries],
    members: list[tuple[str, int]],
) -> dict[str, dict[str, float]]:
    """Cross-sectional z-scores for one peer group on one day.

    ``members`` are ``(market, bar_index)`` pairs for the markets that traded
    that day. Returns ``{market: {feature: z}}``.
    """
    out: dict[str, dict[str, float]] = {market: {} for market, _ in members}
    for feature in FEATURE_KEYS:
        raw: dict[str, float] = {}
        for market, index in members:
            value = panel[market].features[feature][index]
            if value is not None:
                raw[market] = value
        if len(raw) < MIN_CROSS_SECTION:
            continue
        for market, z in rank_z_scores(raw).items():
            out[market][feature] = z
    return out


def group_members_by_day(
    panel: dict[str, MarketSeries],
    group: str,
    *,
    min_index: int = WARMUP_BARS,
) -> dict[str, list[tuple[str, int]]]:
    """Map each day to the ``(market, bar_index)`` members of a peer group."""
    by_day: dict[str, list[tuple[str, int]]] = {}
    for market, series in panel.items():
        if series.group != group:
            continue
        for index in range(min_index, len(series.days)):
            by_day.setdefault(series.days[index], []).append((market, index))
    return by_day


def time_series_z(
    series: MarketSeries,
    index: int,
    *,
    lookback: int = 250,
) -> dict[str, float]:
    """Fallback scoring: rank each feature against the market's own history.

    Used when no peer-group cross-section is available — notably in the
    walk-forward track record, which replays a single market's history in
    isolation. Same rank-based scaling, only the comparison set differs.
    """
    start = max(WARMUP_BARS, index - lookback + 1)
    out: dict[str, float] = {}
    for feature in FEATURE_KEYS:
        values = series.features[feature]
        current = values[index]
        if current is None:
            continue
        history = [v for v in values[start : index + 1] if v is not None]
        if len(history) < 30:
            continue
        below = sum(1 for v in history if v < current)
        equal = sum(1 for v in history if v == current)
        percentile = (below + equal / 2.0) / len(history)
        out[feature] = (percentile - 0.5) * _RANK_SCALE
    return out
