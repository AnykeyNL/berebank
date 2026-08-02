r"""Standalone verification of the GTP56Sol historical-pattern engine.

Run: .venv\Scripts\python test_gtp56sol_analysis.py
"""
import json
import math
import inspect

from app.services import gtp56sol_analysis

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def make_candles(closes, *, start_ts=1_700_000_000_000, spread=0.001, volumes=None):
    """Build API-shape daily candles from close prices."""
    volumes = volumes or [1000.0 + (i % 11) * 10 for i in range(len(closes))]
    return [
        [
            start_ts + i * 86_400_000,
            str(closes[i - 1] if i else closes[i]),
            str(closes[i] * (1 + spread)),
            str(closes[i] * (1 - spread)),
            str(closes[i]),
            str(volumes[i]),
        ]
        for i in range(len(closes))
    ]


def patterned_candles(n=420):
    closes = [100.0]
    for i in range(1, n):
        phase = i % 30
        move = 0.007 if phase < 10 else (-0.006 if phase < 20 else 0.0002)
        closes.append(closes[-1] * (1 + move + 0.0007 * math.sin(i * 0.71)))
    return make_candles(closes)


def finite_tree(value) -> bool:
    if isinstance(value, dict):
        return all(finite_tree(v) for v in value.values())
    if isinstance(value, list):
        return all(finite_tree(v) for v in value)
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return True
        return math.isfinite(number)
    return True


print("horizons")
check("maps one day", gtp56sol_analysis.horizon_bars("1d") == 1)
check("maps one week", gtp56sol_analysis.horizon_bars("1w") == 5)
check("maps one month", gtp56sol_analysis.horizon_bars("1m") == 21)
try:
    gtp56sol_analysis.horizon_bars("30d")
except ValueError:
    check("rejects unknown horizon", True)
else:
    check("rejects unknown horizon", False)

print("volatility-aware labels")
check("return above floor band is up", gtp56sol_analysis.label_outcome(100, 100.6, 0.2, 1) == "up")
check("return inside floor band is sideways", gtp56sol_analysis.label_outcome(100, 100.4, 0.2, 1) == "sideways")
check("volatility widens down threshold", gtp56sol_analysis.label_outcome(100, 98.5, 2.0, 5) == "sideways")
check("move beyond volatility band is down", gtp56sol_analysis.label_outcome(100, 97.0, 2.0, 5) == "down")

print("feature snapshots and leakage")
candles = patterned_candles()
cut = 250
snapshot = gtp56sol_analysis.build_feature_snapshot(candles, cut)
mutated = [row[:] for row in candles]
for row in mutated[cut + 1 :]:
    row[1:6] = ["999999", "1000000", "1", "777777", "999999999"]
snapshot_mutated = gtp56sol_analysis.build_feature_snapshot(mutated, cut)
check("future candles do not alter earlier snapshot", snapshot == snapshot_mutated)
early = gtp56sol_analysis.forecast(candles, "1w", as_of_index=cut)
early_mutated = gtp56sol_analysis.forecast(mutated, "1w", as_of_index=cut)
check("future candles do not alter earlier prediction", early == early_mutated)
check("snapshot includes required feature families", {
    "vote_trend", "vote_rsi", "vote_macd", "vote_volatility",
    "vote_levels_volume", "rsi_normalized", "macd_hist_price",
    "bollinger_position", "atr_pct", "support_distance",
    "resistance_distance", "return_1", "return_5", "return_20",
    "realized_volatility_20", "volume_ratio",
}.issubset(snapshot))
matrix_snapshot = gtp56sol_analysis._feature_matrix(candles[: cut + 1])[2][-1]
check("query and candidate feature pipelines are identical", snapshot == matrix_snapshot)

print("robust normalization and missingness")
normalization_candidates = []
for i, value in enumerate((1.0, 2.0, 3.0, 1000.0)):
    features = {name: 0.0 for name in gtp56sol_analysis.FEATURE_NAMES}
    features["return_1"] = value
    normalization_candidates.append(
        gtp56sol_analysis.Candidate(i, i + 1, features, "up")
    )
centers, scales = gtp56sol_analysis._fit_normalization(normalization_candidates)
check("normalization uses median center", math.isclose(centers["return_1"], 2.5))
check("normalization uses scaled MAD", math.isclose(scales["return_1"], 1.4826, rel_tol=1e-6))
discrete_candidates = []
for i, vote in enumerate((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, -1.0)):
    features = {name: 0.0 for name in gtp56sol_analysis.FEATURE_NAMES}
    features["vote_trend"] = vote
    discrete_candidates.append(gtp56sol_analysis.Candidate(i, i + 1, features, "up"))
_, discrete_scales = gtp56sol_analysis._fit_normalization(discrete_candidates)
check(
    "degenerate discrete MAD falls back to useful scale",
    discrete_scales["vote_trend"] > 0.1,
    f"got {discrete_scales['vote_trend']}",
)
has_presence_distance = hasattr(gtp56sol_analysis, "_presence_aware_distance")
check("presence-aware distance helper exists", has_presence_distance)
if has_presence_distance:
    complete = {name: 0.0 for name in gtp56sol_analysis.FEATURE_NAMES}
    sparse = {name: None for name in gtp56sol_analysis.FEATURE_NAMES}
    sparse["return_1"] = 0.0
    partial = complete.copy()
    partial["support_distance"] = None
    exact_distance = gtp56sol_analysis._presence_aware_distance(complete, complete, centers, scales)
    sparse_distance = gtp56sol_analysis._presence_aware_distance(complete, sparse, centers, scales)
    partial_distance = gtp56sol_analysis._presence_aware_distance(complete, partial, centers, scales)
    check("too-little overlap is rejected", sparse_distance is None)
    check("unshared dimensions carry a penalty", exact_distance == 0.0 and partial_distance > exact_distance)
    complete_candidates = [
        gtp56sol_analysis.Candidate(i * 2, i * 2 + 1, complete, "up")
        for i in range(30)
    ]
    sparse_candidates = [
        gtp56sol_analysis.Candidate(1000 + i * 2, 1001 + i * 2, sparse, "down")
        for i in range(30)
    ]
    missing_evidence = gtp56sol_analysis._probabilities(
        complete,
        complete_candidates + sparse_candidates,
    )
    check(
        "missing candidates cannot inflate similarity or evidence",
        missing_evidence is not None
        and missing_evidence[1] == 30
        and missing_evidence[2] <= missing_evidence[1],
    )
    underlap_evidence = gtp56sol_analysis._probabilities(
        complete,
        complete_candidates[:29] + sparse_candidates,
    )
    check("minimum sample floor applies after overlap filtering", underlap_evidence is None)
    realistic_candidates = gtp56sol_analysis._candidates(candles, 5)
    realistic_centers, realistic_scales = gtp56sol_analysis._fit_normalization(realistic_candidates)
    realistic_current = gtp56sol_analysis.build_feature_snapshot(candles)
    realistic_distances = sorted(
        distance
        for candidate in realistic_candidates
        if (distance := gtp56sol_analysis._presence_aware_distance(
            realistic_current,
            candidate.features,
            realistic_centers,
            realistic_scales,
        )) is not None
    )
    p95_distance = realistic_distances[int(len(realistic_distances) * 0.95)]
    check("realistic distance distribution stays bounded", p95_distance < 100, f"got {p95_distance}")
else:
    check("too-little overlap is rejected", False)
    check("unshared dimensions carry a penalty", False)
    check("missing candidates cannot inflate similarity or evidence", False)
    check("minimum sample floor applies after overlap filtering", False)
    check("realistic distance distribution stays bounded", False)

print("forecast")
result = gtp56sol_analysis.forecast(candles, "1w")
probabilities = {key: float(value) for key, value in result["probabilities"].items()}
check("forecast status is ok", result["status"] == "ok", f"got {result}")
check("probabilities sum to one", math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-9), f"got {probabilities}")
check("smoothing keeps every class probability open", all(0.0 < value < 1.0 for value in probabilities.values()))
check("result is deterministic", result == gtp56sol_analysis.forecast(candles, "1w"))
check("returns three structured drivers", len(result["drivers"]) == 3 and all(
    set(driver) == {"code", "params"} for driver in result["drivers"]
))
check("walk-forward exposes evidence", set(result["validation"]) >= {
    "evaluated_samples", "directional_accuracy", "period_start", "period_end",
})
check("validation exposes majority baseline", "majority_baseline_accuracy" in result["validation"])
check("forecast exposes candidate pool size", "candidate_pool_size" in result)
check("forecast exposes effective samples", "effective_sample_count" in result)
if "effective_sample_count" in result:
    check("effective evidence does not exceed raw neighbors", result["effective_sample_count"] <= result["sample_count"])
else:
    check("effective evidence does not exceed raw neighbors", False)
check("period and horizon exposed", result["period_start"] < result["period_end"] and result["horizon"] == "1w")
check("JSON payload has no non-finite values", finite_tree(result) and "NaN" not in json.dumps(result))

trend = make_candles([100.0 * (1.008 ** i) for i in range(320)], spread=0.0005)
trend_result = gtp56sol_analysis.forecast(trend, "1d")
check("trend-like history produces bullish result", trend_result["direction"] == "bullish", f"got {trend_result}")
check("trend-like up probability wins", float(trend_result["probabilities"]["up"]) > 0.8)

weak = gtp56sol_analysis.forecast(candles[:105], "1d")
check("weak evidence is never high confidence", weak["confidence"] != "high", f"got {weak['confidence']}")

print("direction and evidence thresholds")
check("sideways winner is neutral", gtp56sol_analysis._direction({"up": 0.2, "sideways": 0.6, "down": 0.2}) == "neutral")
check("small up/down lead is neutral", gtp56sol_analysis._direction({"up": 0.44, "sideways": 0.2, "down": 0.36}) == "neutral")
check("outlook score is up minus down on -100..+100", gtp56sol_analysis.outlook_score({"up": 0.6, "sideways": 0.1, "down": 0.3}) == 30)
check("outlook score rounds net probability", gtp56sol_analysis.outlook_score({"up": 0.5149, "sideways": 0.2451, "down": 0.24}) == 27)
outlook = gtp56sol_analysis.forecast_outlook(candles, "1w")
check("forecast outlook status is ok", outlook["status"] == "ok")
check("forecast outlook exposes score", isinstance(outlook.get("score"), int))
check("forecast outlook skips walk-forward", outlook["confidence"] in {"low", "medium", "high"})
floor_29 = gtp56sol_analysis.forecast(patterned_candles(80), "1d")
floor_30 = gtp56sol_analysis.forecast(patterned_candles(81), "1d")
check("29 candidates is insufficient", floor_29["status"] == "insufficient_history")
check("30 candidates is sufficient", floor_30["status"] == "ok")
large = gtp56sol_analysis.forecast(patterned_candles(500), "1d")
check("nearest-neighbor sample is capped at 100", large["sample_count"] == 100)

confidence_parameters = set(inspect.signature(gtp56sol_analysis._confidence).parameters)
new_confidence_api = {
    "effective_sample_count", "validation_accuracy", "baseline_accuracy",
    "evaluated_samples", "effective_evaluated_samples",
}.issubset(confidence_parameters)
check("confidence accepts independent and baseline evidence", new_confidence_api)
supports_scope_confidence = "source_scope" in confidence_parameters
check("confidence accepts source scope", supports_scope_confidence)
if new_confidence_api:
    confidence_args = dict(
        sample_count=100,
        effective_sample_count=100,
        average_similarity=0.8,
        probabilities={"up": 0.8, "sideways": 0.1, "down": 0.1},
        validation_accuracy=0.8,
        baseline_accuracy=0.5,
        evaluated_samples=60,
        effective_evaluated_samples=40,
    )
    check("strong independent evidence can be high", gtp56sol_analysis._confidence(**confidence_args) == "high")
    if supports_scope_confidence:
        check(
            "asset-class evidence caps confidence at medium",
            gtp56sol_analysis._confidence(**(confidence_args | {"source_scope": "asset_class"})) == "medium",
        )
    else:
        check("asset-class evidence caps confidence at medium", False)
    medium_args = confidence_args | {
        "sample_count": 80,
        "effective_sample_count": 20,
        "average_similarity": 0.5,
        "probabilities": {"up": 0.65, "sideways": 0.2, "down": 0.15},
        "validation_accuracy": 0.62,
        "baseline_accuracy": 0.55,
        "evaluated_samples": 30,
        "effective_evaluated_samples": 15,
    }
    check("moderate evidence produces medium confidence", gtp56sol_analysis._confidence(**medium_args) == "medium")
    check("weak raw samples block confidence", gtp56sol_analysis._confidence(**(confidence_args | {"sample_count": 20})) == "low")
    check("weak similarity blocks confidence", gtp56sol_analysis._confidence(**(confidence_args | {"average_similarity": 0.2})) == "low")
    check("weak effective samples block confidence", gtp56sol_analysis._confidence(**(confidence_args | {"effective_sample_count": 10})) == "low")
    check("weak raw validation count blocks confidence", gtp56sol_analysis._confidence(**(confidence_args | {"evaluated_samples": 5})) == "low")
    check("weak validation samples block confidence", gtp56sol_analysis._confidence(**(confidence_args | {"effective_evaluated_samples": 5})) == "low")
    check("weak probability separation blocks confidence", gtp56sol_analysis._confidence(**(
        confidence_args | {"probabilities": {"up": 0.4, "sideways": 0.35, "down": 0.25}}
    )) == "low")
    check("weak directional accuracy blocks confidence", gtp56sol_analysis._confidence(**(
        confidence_args | {"validation_accuracy": 0.45, "baseline_accuracy": 0.2}
    )) == "low")
    check("no baseline improvement blocks confidence", gtp56sol_analysis._confidence(**(
        confidence_args | {"validation_accuracy": 0.8, "baseline_accuracy": 0.79}
    )) == "low")
else:
    for name in (
        "strong independent evidence can be high",
        "asset-class evidence caps confidence at medium",
        "moderate evidence produces medium confidence",
        "weak raw samples block confidence",
        "weak similarity blocks confidence",
        "weak effective samples block confidence",
        "weak raw validation count blocks confidence",
        "weak validation samples block confidence",
        "weak probability separation blocks confidence",
        "weak directional accuracy blocks confidence",
        "no baseline improvement blocks confidence",
    ):
        check(name, False)

print("insufficient history and fallback")
short = candles[-90:]
insufficient = gtp56sol_analysis.forecast(short, "1m")
check("insufficient history has explicit status", insufficient["status"] == "insufficient_history")
check("insufficient history fabricates no probabilities", insufficient["probabilities"] is None)
fallback = gtp56sol_analysis.forecast(
    short,
    "1m",
    fallback_candles_by_market={"PEER-EUR": candles},
)
check("fallback identifies asset-class scope", fallback["status"] == "ok" and fallback["source_scope"] == "asset_class")
check("asset history keeps asset scope", result["source_scope"] == "asset")
check(
    "primary period stays on asset window",
    fallback["period_start"] == gtp56sol_analysis._iso(int(short[0][0]))
    and fallback["period_end"] == gtp56sol_analysis._iso(int(short[-1][0])),
)
check(
    "fallback evidence period describes candidate pool",
    fallback["evidence_period_start"] < fallback["period_start"]
    and fallback["evidence_period_end"] <= fallback["period_end"],
)
if "candidate_pool_size" in fallback:
    peer_only_size = len(gtp56sol_analysis._candidates(candles, 21))
    check("fallback pools asset and peer candidates", fallback["candidate_pool_size"] > peer_only_size)
else:
    check("fallback pools asset and peer candidates", False)
historical_short = candles[180:250]
fallback_mutated = [row[:] for row in candles]
for row in fallback_mutated[250:]:
    row[4] = str(float(row[4]) * 20)
fallback_causal = gtp56sol_analysis.forecast(
    historical_short,
    "1d",
    fallback_candles_by_market={"PEER-EUR": candles},
)
fallback_causal_mutated = gtp56sol_analysis.forecast(
    historical_short,
    "1d",
    fallback_candles_by_market={"PEER-EUR": fallback_mutated},
)
check("fallback excludes candles after forecast time", fallback_causal == fallback_causal_mutated)

duplicate_features = {name: 0.0 for name in gtp56sol_analysis.FEATURE_NAMES}
duplicate_candidates = [
    gtp56sol_analysis.Candidate(i * 2, i * 2 + 1, duplicate_features, "up")
    for i in range(30)
]
duplicate_candidates.append(duplicate_candidates[-1])
deduplicated_evidence = gtp56sol_analysis._probabilities(duplicate_features, duplicate_candidates)
check(
    "duplicate source windows do not enlarge candidate pool",
    deduplicated_evidence is not None and deduplicated_evidence[4] == 30,
)

print("safe degradation")
flat = make_candles([100.0] * 220, volumes=[0.0] * 220)
try:
    flat_result = gtp56sol_analysis.forecast(flat, "1d")
    check("flat zero-volume history is safe", flat_result["status"] == "ok" and finite_tree(flat_result))
except Exception as exc:
    check("flat zero-volume history is safe", False, repr(exc))
bad_bars = [row[:] for row in candles]
bad_bars[20][4] = "0"
bad_bars[40][2] = "nan"
bad_bars[60] = [bad_bars[60][0], "broken"]
try:
    bad_result = gtp56sol_analysis.forecast(bad_bars, "1d")
    check("invalid stored bars degrade safely", bad_result["status"] in {"ok", "insufficient_history"})
except Exception as exc:
    check("invalid stored bars degrade safely", False, repr(exc))
check("decimals use ten significant digits", gtp56sol_analysis._s(1.234567890123) == "1.23456789")

print("walk-forward causality")
validation_end = result["validation"]["period_end"]
check("walk-forward has evaluated samples", result["validation"]["evaluated_samples"] > 0)
check("walk-forward period ends before current setup", validation_end < result["period_end"])
check("as-of validation unchanged by future mutation", early["validation"] == early_mutated["validation"])
walk_candidates = gtp56sol_analysis._candidates(candles, 5)
check(
    "overlapping windows reduce effective samples",
    gtp56sol_analysis._effective_sample_count(walk_candidates[:30]) < 30,
)
expected_newest_fold = gtp56sol_analysis._iso(walk_candidates[-1].timestamp)
check("validation sampling includes newest fold", result["validation"]["period_end"] == expected_newest_fold)
has_fold_predictor = hasattr(gtp56sol_analysis, "_walk_forward_prediction")
check("direct prior-only fold predictor exists", has_fold_predictor)
if has_fold_predictor:
    target = walk_candidates[180]
    prediction = gtp56sol_analysis._walk_forward_prediction(target, walk_candidates)
    changed_future = list(walk_candidates)
    future = changed_future[-1]
    changed_future[-1] = gtp56sol_analysis.Candidate(
        future.timestamp,
        future.outcome_end_timestamp,
        {name: 999999.0 for name in gtp56sol_analysis.FEATURE_NAMES},
        "down",
    )
    check(
        "walk-forward fold ignores future candidates",
        prediction == gtp56sol_analysis._walk_forward_prediction(target, changed_future),
    )
else:
    check("walk-forward fold ignores future candidates", False)

print("supplementary features")
check("engine version bumped", gtp56sol_analysis.ENGINE_VERSION == "2")
check("twenty feature dimensions", len(gtp56sol_analysis.FEATURE_NAMES) == 20)
ctx = {
    "vix_by_day": {"2024-06-01": 18.0},
    "yield_spread_by_day": {"2024-06-01": 0.3},
    "vix_level": 18.0,
    "yield_spread": 0.3,
    "earnings_near": True,
    "insider_signal": "bullish",
}
candles = patterned_candles(120)
snapshot = gtp56sol_analysis.build_feature_snapshot(candles, context=ctx)
check("context adds macro features", snapshot["vix_normalized"] is not None)
check("earnings proximity on current bar", snapshot["earnings_proximity"] == 1.0)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
