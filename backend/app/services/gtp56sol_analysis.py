"""Explainable historical-pattern forecasts over daily OHLCV candles.

The engine is deliberately provider-independent and deterministic. Feature
snapshots are causal, historical outcomes are volatility-band labels, and
walk-forward validation fits every fold from expanding prior data only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean, median

from app.services import analysis

ENGINE_VERSION = "3"
HORIZONS = {"1d": 1, "1w": 5, "1m": 21}
MIN_SAMPLES = 30
MAX_NEIGHBORS = 100
FEATURE_WARMUP = 50
MAX_VALIDATION_FOLDS = 60
ROBUST_SCALE_FLOOR = 1e-6
MIN_SHARED_FEATURES = 8
MISSING_DIMENSION_PENALTY = 1.0
# Reserve three percent of probability mass for unseen classes. This avoids
# presenting historical frequencies as impossible/certain outcomes.
PROBABILITY_SMOOTHING = 0.03

FEATURE_NAMES = (
    "vote_trend",
    "vote_rsi",
    "vote_macd",
    "vote_volatility",
    "vote_levels_volume",
    "rsi_normalized",
    "macd_hist_price",
    "bollinger_position",
    "atr_pct",
    "support_distance",
    "resistance_distance",
    "return_1",
    "return_5",
    "return_20",
    "realized_volatility_20",
    "volume_ratio",
    "vix_normalized",
    "yield_spread",
    "earnings_proximity",
    "insider_activity",
    "funding_normalized",
    "oi_change_24h",
)


@dataclass(frozen=True)
class Candidate:
    timestamp: int
    outcome_end_timestamp: int
    features: dict[str, float | None]
    label: str
    source: str = "asset"


def _sanitize_candles(candles: list[list]) -> list[list]:
    """Drop malformed stored bars without reordering the usable chronology."""
    clean: list[list] = []
    previous_timestamp: int | None = None
    for candle in candles:
        try:
            if len(candle) < 6:
                continue
            timestamp = int(candle[0])
            open_, high, low, close, volume = (float(candle[i]) for i in range(1, 6))
        except (TypeError, ValueError, OverflowError):
            continue
        values = (open_, high, low, close, volume)
        if not all(math.isfinite(value) for value in values):
            continue
        if (
            timestamp < 0
            or (previous_timestamp is not None and timestamp <= previous_timestamp)
            or min(open_, high, low, close) <= 0
            or volume < 0
            or high < low
        ):
            continue
        clean.append([timestamp, open_, high, low, close, volume])
        previous_timestamp = timestamp
    return clean


def horizon_bars(horizon: str) -> int:
    """Return the number of forward daily bars for a supported horizon."""
    try:
        return HORIZONS[horizon]
    except KeyError as exc:
        raise ValueError(f"unsupported horizon: {horizon!r}") from exc


def has_sufficient_asset_history_count(candle_count: int, horizon: str) -> bool:
    """Whether a valid-row count can supply the engine's minimum candidates."""
    bars = horizon_bars(horizon)
    return max(0, candle_count - bars - FEATURE_WARMUP) >= MIN_SAMPLES


def has_sufficient_asset_history(candles: list[list], horizon: str) -> bool:
    """Whether primary rows can supply the engine's minimum candidates.

    This is a lightweight cache/fallback planning helper. It intentionally
    mirrors the candidate-index bounds without calculating any indicators.
    """
    return has_sufficient_asset_history_count(
        len(_sanitize_candles(candles)),
        horizon,
    )


def label_outcome(
    current_close: float,
    forward_close: float,
    atr_pct: float | None,
    bars: int,
) -> str:
    """Classify a forward return with a volatility-aware neutral band."""
    if bars <= 0:
        raise ValueError("bars must be positive")
    if not all(math.isfinite(value) for value in (current_close, forward_close)):
        raise ValueError("prices must be finite")
    if current_close <= 0:
        raise ValueError("current_close must be positive")
    safe_atr_pct = atr_pct if atr_pct is not None and math.isfinite(atr_pct) else 0.0
    band_pct = max(0.5, 0.5 * max(0.0, safe_atr_pct) * math.sqrt(bars))
    return_pct = (forward_close / current_close - 1.0) * 100.0
    if return_pct > band_pct:
        return "up"
    if return_pct < -band_pct:
        return "down"
    return "sideways"


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _return_pct(closes: list[float], index: int, bars: int) -> float | None:
    if index < bars or closes[index - bars] <= 0:
        return None
    return _finite((closes[index] / closes[index - bars] - 1.0) * 100.0)


def _vote(signal: str) -> float:
    return 1.0 if signal == "bullish" else -1.0 if signal == "bearish" else 0.0


def _derived_vote_features(
    index: int,
    closes: list[float],
    sma20: list[float | None],
    sma50: list[float | None],
    rsi_values: list[float | None],
    hist: list[float | None],
    upper: list[float | None],
    lower: list[float | None],
    support_distance: float | None,
    resistance_distance: float | None,
) -> tuple[float, float, float, float, float]:
    price = closes[index]
    fast, slow = sma20[index], sma50[index]
    trend = 0.0
    if fast is not None and slow is not None:
        trend = 1.0 if price > slow and fast > slow else -1.0 if price < slow and fast < slow else 0.0

    current_rsi = rsi_values[index]
    rsi_vote = 0.0
    if current_rsi is not None:
        rsi_vote = -1.0 if current_rsi > 70 else 1.0 if current_rsi < 30 else 0.0

    current_hist = hist[index]
    macd_vote = 0.0 if current_hist is None else 1.0 if current_hist > 0 else -1.0 if current_hist < 0 else 0.0

    up, low = upper[index], lower[index]
    volatility_vote = 0.0
    if up is not None and low is not None:
        volatility_vote = -1.0 if price >= up else 1.0 if price <= low else 0.0

    levels_vote = 0.0
    if support_distance is not None and support_distance <= 1.5:
        levels_vote = 1.0
    elif resistance_distance is not None and resistance_distance <= 1.5:
        levels_vote = -1.0
    return trend, rsi_vote, macd_vote, volatility_vote, levels_vote


def _macro_feature_row(
    context: dict | None,
    timestamp_ms: int,
    *,
    current_only: bool = False,
) -> dict[str, float | None]:
    if context is None:
        return {
            "vix_normalized": None,
            "yield_spread": None,
            "earnings_proximity": None,
            "insider_activity": None,
        }
    from .td_context import macro_features_at

    return macro_features_at(context, timestamp_ms, current_only=current_only)


def _feature_matrix(
    candles: list[list],
    context: dict | None = None,
    *,
    current_only_macro: bool = False,
) -> tuple[list[int], list[float], list[dict[str, float | None]]]:
    candles = _sanitize_candles(candles)
    timestamps = [int(candle[0]) for candle in candles]
    highs = [float(candle[2]) for candle in candles]
    lows = [float(candle[3]) for candle in candles]
    closes = [float(candle[4]) for candle in candles]
    volumes = [float(candle[5]) for candle in candles]
    rsi_values = analysis.rsi(closes)
    _, _, hist = analysis.macd(closes)
    _, upper, lower = analysis.bollinger(closes)
    atr_values = analysis.atr(highs, lows, closes)
    sma20 = analysis.sma(closes, 20)
    sma50 = analysis.sma(closes, 50)
    rows: list[dict[str, float | None]] = []

    for index, price in enumerate(closes):
        window_start = max(0, index - 59)
        levels = analysis.pivot_levels(
            highs[window_start : index + 1],
            lows[window_start : index + 1],
        )
        supports = [level["price"] for level in levels if level["price"] < price]
        resistances = [level["price"] for level in levels if level["price"] >= price]
        support_distance = (
            (price - max(supports)) / price * 100.0
            if supports and price > 0
            else None
        )
        resistance_distance = (
            (min(resistances) - price) / price * 100.0
            if resistances and price > 0
            else None
        )

        current_rsi = rsi_values[index]
        current_hist = hist[index]
        current_atr = atr_values[index]
        up, low = upper[index], lower[index]
        band_position = (
            (price - low) / (up - low)
            if up is not None and low is not None and up != low
            else 0.5 if up is not None and low is not None
            else None
        )
        volume_window = volumes[max(0, index - 19) : index + 1]
        recent_volume = volumes[max(0, index - 4) : index + 1]
        average_volume = fmean(volume_window) if volume_window else 0.0
        volume_ratio = fmean(recent_volume) / average_volume if average_volume > 0 else None

        daily_returns = []
        for position in range(max(1, index - 19), index + 1):
            previous = closes[position - 1]
            if previous > 0:
                daily_returns.append(math.log(closes[position] / previous))
        realized_volatility = None
        if len(daily_returns) >= 2:
            mean_return = fmean(daily_returns)
            variance = fmean((value - mean_return) ** 2 for value in daily_returns)
            realized_volatility = math.sqrt(variance) * math.sqrt(20.0) * 100.0

        votes = _derived_vote_features(
            index,
            closes,
            sma20,
            sma50,
            rsi_values,
            hist,
            upper,
            lower,
            support_distance,
            resistance_distance,
        )
        macro = _macro_feature_row(
            context,
            timestamps[index],
            current_only=current_only_macro and index == len(candles) - 1,
        )
        rows.append({
            "vote_trend": votes[0],
            "vote_rsi": votes[1],
            "vote_macd": votes[2],
            "vote_volatility": votes[3],
            "vote_levels_volume": votes[4],
            "rsi_normalized": _finite((current_rsi - 50.0) / 50.0) if current_rsi is not None else None,
            "macd_hist_price": _finite(current_hist / price) if current_hist is not None and price != 0 else None,
            "bollinger_position": _finite(band_position),
            "atr_pct": _finite(current_atr / price * 100.0) if current_atr is not None and price != 0 else None,
            "support_distance": _finite(support_distance),
            "resistance_distance": _finite(resistance_distance),
            "return_1": _return_pct(closes, index, 1),
            "return_5": _return_pct(closes, index, 5),
            "return_20": _return_pct(closes, index, 20),
            "realized_volatility_20": _finite(realized_volatility),
            "volume_ratio": _finite(volume_ratio),
            **macro,
        })
    return timestamps, closes, rows


def build_feature_snapshot(
    candles: list[list],
    index: int | None = None,
    context: dict | None = None,
) -> dict[str, float | None]:
    """Build a causal feature snapshot at ``index``.

    The input is sliced before any indicator is calculated. This makes the
    no-future-data guarantee explicit even when callers pass a longer array.
    """
    if not candles:
        raise ValueError("candles must not be empty")
    if index is None:
        index = len(candles) - 1
    if index < 0 or index >= len(candles):
        raise IndexError("snapshot index outside candles")
    sliced = candles[: index + 1]
    _, _, rows = _feature_matrix(
        sliced,
        context,
        current_only_macro=True,
    )
    if not rows:
        raise ValueError("no usable candles at or before index")
    return rows[-1].copy()


def _candidates(
    candles: list[list],
    bars: int,
    source: str = "asset",
    context: dict | None = None,
) -> list[Candidate]:
    candles = _sanitize_candles(candles)
    timestamps, closes, rows = _feature_matrix(candles, context)
    candidates: list[Candidate] = []
    for index in range(FEATURE_WARMUP, len(candles) - bars):
        atr_pct = rows[index]["atr_pct"]
        candidates.append(Candidate(
            timestamp=timestamps[index],
            outcome_end_timestamp=timestamps[index + bars],
            features=rows[index],
            label=label_outcome(closes[index], closes[index + bars], atr_pct, bars),
            source=source,
        ))
    return candidates


def _fit_normalization(candidates: list[Candidate]) -> tuple[dict[str, float], dict[str, float]]:
    centers: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in FEATURE_NAMES:
        values = [
            value
            for candidate in candidates
            if (value := candidate.features.get(name)) is not None and math.isfinite(value)
        ]
        center = median(values) if values else 0.0
        mad = median(abs(value - center) for value in values) if values else 0.0
        scale = mad * 1.4826
        if scale <= ROBUST_SCALE_FLOOR and len(values) > 1:
            scale = math.sqrt(fmean((value - center) ** 2 for value in values))
        centers[name] = center
        scales[name] = max(scale, ROBUST_SCALE_FLOOR)
    return centers, scales


def _presence_aware_distance(
    left: dict[str, float | None],
    right: dict[str, float | None],
    centers: dict[str, float],
    scales: dict[str, float],
) -> float | None:
    """Robust normalized distance without treating missing values as matches."""
    squared_differences: list[float] = []
    unshared = 0
    for name in FEATURE_NAMES:
        left_value = left.get(name)
        right_value = right.get(name)
        left_present = left_value is not None and math.isfinite(left_value)
        right_present = right_value is not None and math.isfinite(right_value)
        if left_present and right_present:
            left_normalized = (left_value - centers[name]) / scales[name]
            right_normalized = (right_value - centers[name]) / scales[name]
            squared_differences.append((left_normalized - right_normalized) ** 2)
        elif left_present != right_present:
            unshared += 1
    if len(squared_differences) < MIN_SHARED_FEATURES:
        return None
    shared_distance = math.sqrt(fmean(squared_differences))
    missing_penalty = MISSING_DIMENSION_PENALTY * unshared / len(FEATURE_NAMES)
    return shared_distance + missing_penalty


def _effective_sample_count(candidates: list[Candidate]) -> int:
    """Count non-overlapping outcome windows independently per source."""
    last_end_by_source: dict[str, int] = {}
    count = 0
    for candidate in sorted(
        candidates,
        key=lambda item: (item.outcome_end_timestamp, item.timestamp, item.source),
    ):
        last_end = last_end_by_source.get(candidate.source)
        if last_end is None or candidate.timestamp > last_end:
            count += 1
            last_end_by_source[candidate.source] = candidate.outcome_end_timestamp
    return count


def _deduplicate_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Keep one deterministic candidate per source and outcome window."""
    unique: list[Candidate] = []
    seen: set[tuple[str, int, int]] = set()
    for candidate in candidates:
        key = (
            candidate.source,
            candidate.timestamp,
            candidate.outcome_end_timestamp,
        )
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _probabilities(
    current: dict[str, float | None],
    candidates: list[Candidate],
) -> tuple[dict[str, float], int, int, float, int] | None:
    candidates = _deduplicate_candidates(candidates)
    centers, scales = _fit_normalization(candidates)
    ranked: list[tuple[float, Candidate]] = []
    for candidate in candidates:
        distance = _presence_aware_distance(current, candidate.features, centers, scales)
        if distance is None:
            continue
        ranked.append((distance, candidate))
    ranked.sort(key=lambda item: (item[0], item[1].timestamp, item[1].label))
    if len(ranked) < MIN_SAMPLES:
        return None
    nearest = ranked[:MAX_NEIGHBORS]
    totals = {"up": 0.0, "sideways": 0.0, "down": 0.0}
    similarities = []
    for distance, candidate in nearest:
        similarity = 1.0 / (1.0 + distance)
        weight = similarity * similarity
        totals[candidate.label] += weight
        similarities.append(similarity)
    total_weight = sum(totals.values())
    smoothing_per_class = PROBABILITY_SMOOTHING / 3.0
    empirical_up = totals["up"] / total_weight
    empirical_sideways = totals["sideways"] / total_weight
    up = (1.0 - PROBABILITY_SMOOTHING) * empirical_up + smoothing_per_class
    sideways = (
        (1.0 - PROBABILITY_SMOOTHING) * empirical_sideways
        + smoothing_per_class
    )
    down = 1.0 - up - sideways
    probabilities = {
        "up": up,
        "sideways": sideways,
        "down": down,
    }
    selected_candidates = [candidate for _, candidate in nearest]
    return (
        probabilities,
        len(nearest),
        _effective_sample_count(selected_candidates),
        fmean(similarities),
        len(candidates),
    )


def _direction(probabilities: dict[str, float]) -> str:
    if probabilities["sideways"] >= max(probabilities["up"], probabilities["down"]):
        return "neutral"
    if abs(probabilities["up"] - probabilities["down"]) < 0.10:
        return "neutral"
    return "bullish" if probabilities["up"] > probabilities["down"] else "bearish"


def outlook_score(probabilities: dict[str, float]) -> int:
    """Net directional score on -100..+100 from Up minus Down probability mass."""
    return round((probabilities["up"] - probabilities["down"]) * 100.0)


def _list_confidence(
    sample_count: int,
    effective_sample_count: int,
    average_similarity: float,
    probabilities: dict[str, float],
) -> str:
    """Conservative confidence for list views without walk-forward validation."""
    ordered = sorted(probabilities.values(), reverse=True)
    separation = ordered[0] - ordered[1]
    if (
        sample_count >= 100
        and effective_sample_count >= 30
        and average_similarity >= 0.55
        and separation >= 0.20
    ):
        return "high"
    if (
        sample_count >= 60
        and effective_sample_count >= 15
        and average_similarity >= 0.40
        and separation >= 0.10
    ):
        return "medium"
    return "low"


def forecast_outlook(
    candles: list[list],
    horizon: str,
    context: dict | None = None,
) -> dict:
    """Fast direction/score/confidence summary for list views."""
    bars = horizon_bars(horizon)
    asset_candles = _sanitize_candles(candles)
    if len(asset_candles) <= FEATURE_WARMUP:
        return {"status": "insufficient_history"}
    candidates = _deduplicate_candidates(_candidates(asset_candles, bars, source="asset", context=context))
    if len(candidates) < MIN_SAMPLES:
        return {"status": "insufficient_history"}
    current = build_feature_snapshot(asset_candles, context=context)
    evidence = _probabilities(current, candidates)
    if evidence is None:
        return {"status": "insufficient_history"}
    (
        probabilities,
        sample_count,
        effective_sample_count,
        average_similarity,
        _candidate_pool_size,
    ) = evidence
    return {
        "status": "ok",
        "direction": _direction(probabilities),
        "score": outlook_score(probabilities),
        "confidence": _list_confidence(
            sample_count,
            effective_sample_count,
            average_similarity,
            probabilities,
        ),
    }


def _iso(timestamp_ms: int | None) -> str | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000.0, timezone.utc).isoformat().replace("+00:00", "Z")


def _majority_direction(candidates: list[Candidate]) -> str:
    counts = {
        label: sum(candidate.label == label for candidate in candidates)
        for label in ("up", "sideways", "down")
    }
    winner = max(("sideways", "up", "down"), key=lambda label: counts[label])
    return {"up": "bullish", "down": "bearish", "sideways": "neutral"}[winner]


def _walk_forward_prediction(
    target: Candidate,
    candidates: list[Candidate],
) -> tuple[str, str] | None:
    """Predict one fold from candidates whose outcomes ended before it."""
    prior = [
        candidate
        for candidate in candidates
        if candidate.outcome_end_timestamp < target.timestamp
    ]
    if len(prior) < MAX_NEIGHBORS:
        return None
    evidence = _probabilities(target.features, prior)
    if evidence is None:
        return None
    probabilities = evidence[0]
    return _direction(probabilities), _majority_direction(prior)


def _walk_forward(candidates: list[Candidate]) -> dict:
    candidates = _deduplicate_candidates(candidates)
    ordered = sorted(candidates, key=lambda candidate: (candidate.timestamp, candidate.outcome_end_timestamp))
    eligible_targets: list[Candidate] = []
    for target in ordered:
        if sum(candidate.outcome_end_timestamp < target.timestamp for candidate in ordered) >= MAX_NEIGHBORS:
            eligible_targets.append(target)
    if len(eligible_targets) > MAX_VALIDATION_FOLDS:
        last_index = len(eligible_targets) - 1
        eligible_targets = [
            eligible_targets[round(position * last_index / (MAX_VALIDATION_FOLDS - 1))]
            for position in range(MAX_VALIDATION_FOLDS)
        ]

    correct = 0
    baseline_correct = 0
    evaluated = 0
    first_timestamp = last_timestamp = None
    evaluated_targets: list[Candidate] = []
    expected = {"up": "bullish", "down": "bearish", "sideways": "neutral"}
    for target in eligible_targets:
        prediction = _walk_forward_prediction(target, ordered)
        if prediction is None:
            continue
        direction, baseline_direction = prediction
        correct += direction == expected[target.label]
        baseline_correct += baseline_direction == expected[target.label]
        evaluated += 1
        evaluated_targets.append(target)
        first_timestamp = target.timestamp if first_timestamp is None else first_timestamp
        last_timestamp = target.timestamp
    accuracy = correct / evaluated if evaluated else None
    baseline_accuracy = baseline_correct / evaluated if evaluated else None
    return {
        "evaluated_samples": evaluated,
        "effective_evaluated_samples": _effective_sample_count(evaluated_targets),
        "directional_accuracy": accuracy,
        "majority_baseline_accuracy": baseline_accuracy,
        "period_start": _iso(first_timestamp),
        "period_end": _iso(last_timestamp),
    }


def _confidence(
    sample_count: int,
    effective_sample_count: int,
    average_similarity: float,
    probabilities: dict[str, float],
    validation_accuracy: float | None,
    baseline_accuracy: float | None,
    evaluated_samples: int,
    effective_evaluated_samples: int,
    source_scope: str = "asset",
) -> str:
    accuracy = validation_accuracy if validation_accuracy is not None else 0.0
    baseline = baseline_accuracy if baseline_accuracy is not None else 1.0
    improvement = accuracy - baseline
    ordered = sorted(probabilities.values(), reverse=True)
    separation = ordered[0] - ordered[1]
    confidence = "low"
    if (
        sample_count >= 100
        and effective_sample_count >= 30
        and average_similarity >= 0.55
        and separation >= 0.20
        and evaluated_samples >= 30
        and effective_evaluated_samples >= 20
        and accuracy >= 0.65
        and improvement >= 0.05
    ):
        confidence = "high"
    elif (
        sample_count >= 60
        and effective_sample_count >= 15
        and average_similarity >= 0.40
        and separation >= 0.10
        and evaluated_samples >= 15
        and effective_evaluated_samples >= 10
        and accuracy >= 0.50
        and improvement >= 0.02
    ):
        confidence = "medium"
    if source_scope != "asset" and confidence == "high":
        return "medium"
    return confidence


def _s(value: float | None) -> str | None:
    if value is None or not math.isfinite(value):
        return None
    if value == 0:
        return "0"
    text = f"{value:.10g}"
    if "e" in text or "E" in text:
        text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text


def _drivers(
    features: dict[str, float | None],
    probabilities: dict[str, float],
    validation: dict,
    context: dict | None = None,
) -> list[dict]:
    vote_total = sum(features.get(name) or 0.0 for name in FEATURE_NAMES[:5])
    winner = max(("up", "sideways", "down"), key=lambda label: probabilities[label])
    drivers = [
        {
            "code": "historical_probability_leader",
            "params": {"outcome": winner, "probability": _s(probabilities[winner])},
        },
        {
            "code": "technical_vote_balance",
            "params": {"balance": _s(vote_total)},
        },
        {
            "code": "walk_forward_evidence",
            "params": {
                "evaluated_samples": validation["evaluated_samples"],
                "directional_accuracy": validation["directional_accuracy"],
            },
        },
    ]
    if context and context.get("context_type") == "crypto":
        if context.get("fear_greed_index") is not None:
            drivers.append({
                "code": "macro_fear_greed",
                "params": {
                    "index": context.get("fear_greed_index"),
                    "classification": context.get("fear_greed_classification"),
                },
            })
        if context.get("btc_dominance") is not None:
            drivers.append({
                "code": "macro_btc_dominance",
                "params": {"dominance": _s(float(context["btc_dominance"]))},
            })
        if context.get("btc_correlation") is not None:
            drivers.append({
                "code": "macro_btc_correlation",
                "params": {"correlation": _s(float(context["btc_correlation"]))},
            })
        if context.get("stablecoin_supply_change_pct") is not None:
            drivers.append({
                "code": "macro_stablecoin_supply",
                "params": {
                    "change_pct": _s(float(context["stablecoin_supply_change_pct"])),
                },
            })
        if context.get("funding_rate_avg") is not None:
            drivers.append({
                "code": "macro_funding_rate",
                "params": {"funding": _s(float(context["funding_rate_avg"]))},
            })
        if context.get("open_interest_change_percent_24h") is not None:
            drivers.append({
                "code": "macro_open_interest",
                "params": {
                    "change": _s(float(context["open_interest_change_percent_24h"])),
                },
            })
    elif context and context.get("vix_level") is not None:
        drivers.append({
            "code": "macro_vix_context",
            "params": {"vix": _s(float(context["vix_level"]))},
        })
    if context and context.get("context_type") != "crypto" and context.get("yield_spread") is not None:
        drivers.append({
            "code": "macro_yield_spread",
            "params": {"spread": _s(float(context["yield_spread"]))},
        })
    elif context and context.get("context_type") != "crypto" and context.get("us2y_yield") is not None:
        drivers.append({
            "code": "macro_us2y_yield",
            "params": {"us2y": _s(float(context["us2y_yield"]))},
        })
    if context and context.get("earnings_near"):
        drivers.append({
            "code": "earnings_near",
            "params": {"days": context.get("days_to_earnings")},
        })
    if context and context.get("insider_signal") not in (None, "none"):
        drivers.append({
            "code": "insider_activity",
            "params": {
                "signal": context.get("insider_signal"),
                "buys": context.get("insider_buys", 0),
                "sells": context.get("insider_sells", 0),
            },
        })
    return drivers


def _serialized_validation(validation: dict) -> dict:
    return {
        **validation,
        "directional_accuracy": _s(validation["directional_accuracy"]),
        "majority_baseline_accuracy": _s(validation["majority_baseline_accuracy"]),
    }


def _insufficient(
    horizon: str,
    source_scope: str,
    candidate_pool_size: int,
    candles: list[list],
) -> dict:
    return {
        "status": "insufficient_history",
        "horizon": horizon,
        "source_scope": source_scope,
        "probabilities": None,
        "direction": "neutral",
        "confidence": "low",
        "drivers": [],
        "sample_count": 0,
        "effective_sample_count": 0,
        "candidate_pool_size": candidate_pool_size,
        "average_similarity": None,
        "validation": {
            "evaluated_samples": 0,
            "effective_evaluated_samples": 0,
            "directional_accuracy": None,
            "majority_baseline_accuracy": None,
            "period_start": None,
            "period_end": None,
        },
        "period_start": _iso(int(candles[0][0])) if candles else None,
        "period_end": _iso(int(candles[-1][0])) if candles else None,
        "evidence_period_start": None,
        "evidence_period_end": None,
    }


def forecast(
    candles: list[list],
    horizon: str,
    fallback_candles_by_market: dict[str, list[list]] | None = None,
    *,
    as_of_index: int | None = None,
    context: dict | None = None,
) -> dict:
    """Forecast one horizon from historical nearest-pattern outcomes.

    ``as_of_index`` is primarily useful for deterministic backtests. Data after
    that index is discarded before any feature, candidate, or validation work.
    """
    bars = horizon_bars(horizon)
    if not candles:
        return _insufficient(horizon, "asset", 0, candles)
    if as_of_index is None:
        as_of_index = len(candles) - 1
    if as_of_index < 0 or as_of_index >= len(candles):
        raise IndexError("as_of_index outside candles")
    asset_candles = _sanitize_candles(candles[: as_of_index + 1])
    if len(asset_candles) <= FEATURE_WARMUP:
        return _insufficient(horizon, "asset", 0, asset_candles)

    asset_candidates = _candidates(asset_candles, bars, source="asset", context=context)
    source_scope = "asset"
    candidates = asset_candidates
    if len(candidates) < MIN_SAMPLES and fallback_candles_by_market:
        fallback_candidates: list[Candidate] = []
        forecast_timestamp = int(asset_candles[-1][0])
        for market in sorted(fallback_candles_by_market):
            peer_candles = []
            for candle in fallback_candles_by_market[market]:
                try:
                    if int(candle[0]) <= forecast_timestamp:
                        peer_candles.append(candle)
                except (IndexError, TypeError, ValueError, OverflowError):
                    continue
            peer_candles = _sanitize_candles(peer_candles)
            if len(peer_candles) > FEATURE_WARMUP:
                fallback_candidates.extend(
                    _candidates(peer_candles, bars, source=f"peer:{market}", context=context)
                )
        candidates = asset_candidates + fallback_candidates
        source_scope = "asset_class"
    candidates = _deduplicate_candidates(candidates)
    if len(candidates) < MIN_SAMPLES:
        return _insufficient(horizon, source_scope, len(candidates), asset_candles)

    current = build_feature_snapshot(asset_candles, context=context)
    evidence = _probabilities(current, candidates)
    if evidence is None:
        return _insufficient(horizon, source_scope, len(candidates), asset_candles)
    (
        probabilities,
        sample_count,
        effective_sample_count,
        average_similarity,
        candidate_pool_size,
    ) = evidence
    validation = _walk_forward(candidates)
    direction = _direction(probabilities)
    confidence = _confidence(
        sample_count=sample_count,
        effective_sample_count=effective_sample_count,
        average_similarity=average_similarity,
        probabilities=probabilities,
        validation_accuracy=validation["directional_accuracy"],
        baseline_accuracy=validation["majority_baseline_accuracy"],
        evaluated_samples=validation["evaluated_samples"],
        effective_evaluated_samples=validation["effective_evaluated_samples"],
        source_scope=source_scope,
    )
    serialized_validation = _serialized_validation(validation)
    probability_strings = {
        "up": _s(probabilities["up"]),
        "sideways": _s(probabilities["sideways"]),
        "down": _s(probabilities["down"]),
    }
    return {
        "status": "ok",
        "horizon": horizon,
        "source_scope": source_scope,
        "probabilities": probability_strings,
        "direction": direction,
        "confidence": confidence,
        "drivers": _drivers(current, probabilities, serialized_validation, context),
        "sample_count": sample_count,
        "effective_sample_count": effective_sample_count,
        "candidate_pool_size": candidate_pool_size,
        "average_similarity": _s(average_similarity),
        "validation": serialized_validation,
        "period_start": _iso(int(asset_candles[0][0])),
        "period_end": _iso(int(asset_candles[-1][0])),
        "evidence_period_start": _iso(min(candidate.timestamp for candidate in candidates)),
        "evidence_period_end": _iso(max(candidate.outcome_end_timestamp for candidate in candidates)),
    }
