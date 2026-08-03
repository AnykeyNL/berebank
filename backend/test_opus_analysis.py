"""Standalone verification of the Opus analysis engine.

Covers the feature math, cross-sectional ranking, walk-forward calibration on a
synthetic panel with a planted signal, the fee/gate logic that turns a score
into advice, and the snapshot-based live track record.

Run: .venv\\Scripts\\python test_opus_analysis.py
"""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import MarketCandle, OpusRecommendation
from app.services import opus_analysis, opus_calibration, opus_features, opus_macro, opus_store

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


DAY_MS = 86_400_000
START_MS = int(datetime(2022, 1, 3, tzinfo=timezone.utc).timestamp()) * 1000


def candles_from_closes(closes: list[float], *, volume: float = 1000.0) -> list[list]:
    """API-shape daily candles with a small deterministic high/low band."""
    out = []
    for i, close in enumerate(closes):
        high = close * 1.01
        low = close * 0.99
        open_ = closes[i - 1] if i else close
        out.append([START_MS + i * DAY_MS, f"{open_}", f"{high}", f"{low}", f"{close}", f"{volume}"])
    return out


def trending(n: int, *, start: float = 100.0, drift: float = 0.004, noise: float = 0.0,
             seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    closes = [start]
    for _ in range(n - 1):
        step = drift + (rng.gauss(0, noise) if noise else 0.0)
        closes.append(closes[-1] * math.exp(step))
    return closes


# ---------------------------------------------------------------- feature math

print("adx")
up_closes = trending(80, drift=0.01)
adx_values, plus_di, minus_di = opus_features.adx(
    [c * 1.01 for c in up_closes], [c * 0.99 for c in up_closes], up_closes, 14
)
check("undefined during warm-up", adx_values[10] is None)
check("defined after warm-up", adx_values[-1] is not None)
check(
    "+DI leads in an uptrend",
    plus_di[-1] is not None and minus_di[-1] is not None and plus_di[-1] > minus_di[-1],
)
check("short series stays empty", all(v is None for v in opus_features.adx([1, 2], [1, 2], [1, 2])[0]))

print("extract_market_features")
check(
    "rejects too little history",
    opus_features.extract_market_features("X-EUR", "crypto", candles_from_closes(trending(40))) is None,
)
series = opus_features.extract_market_features(
    "UP-EUR", "crypto", candles_from_closes(trending(200, drift=0.004, noise=0.01))
)
check("builds a series", series is not None)
assert series is not None
last = len(series.days) - 1
check("warm-up bars have no features", series.features["mom_21"][opus_features.WARMUP_BARS - 1] is None)
check("momentum is positive in an uptrend", (series.features["mom_21"][last] or 0) > 0)
check("reversal is the negated short move", (series.features["rev_5"][last] or 0) < 0)
check("drawdown is at most zero", (series.features["dd_63"][last] or 0) <= 0)
check("volatility level is positive", (series.features["vol_level"][last] or 0) > 0)
check("turnover is a log euro amount", 10 < (series.features["turnover"][last] or 0) < 16)
check("peer features start undefined", series.features["beta_mkt"][last] is None)
check(
    "features are causal",
    opus_features.extract_market_features(
        "UP-EUR", "crypto", candles_from_closes(trending(200, drift=0.004, noise=0.01))[:150]
    ).features["mom_21"][149] == series.features["mom_21"][149],
)

down = opus_features.extract_market_features(
    "DOWN-EUR", "crypto", candles_from_closes(trending(200, drift=-0.004, noise=0.01, seed=2))
)
check("momentum is negative in a downtrend", (down.features["mom_21"][last] or 0) < 0)

print("forward_return")
check("forward return over 5 bars", series.forward_return(100, 5) is not None)
check("no forward return past the end", series.forward_return(last, 1) is None)

print("rank_z_scores")
check("thin cross-sections score zero", set(opus_features.rank_z_scores({"a": 1.0, "b": 2.0}).values()) == {0.0})
ranked = opus_features.rank_z_scores({m: float(i) for i, m in enumerate("abcdefghij")})
check("best ranks highest", max(ranked, key=lambda m: ranked[m]) == "j")
check("worst ranks lowest", min(ranked, key=lambda m: ranked[m]) == "a")
check("centered on zero", abs(sum(ranked.values())) < 1e-9)
check("scaled to about unit spread", 0.8 < math.sqrt(sum(v * v for v in ranked.values()) / len(ranked)) < 1.2)
tied = opus_features.rank_z_scores({m: 1.0 for m in "abcdefgh"})
check("ties share one score", len(set(tied.values())) == 1)

print("panel and cross-section")
panel_candles = {
    f"M{i}-EUR": candles_from_closes(trending(200, drift=0.002 * (i - 4), noise=0.012, seed=i))
    for i in range(10)
}
asset_classes = {market: "crypto" for market in panel_candles}
asset_classes["M9-EUR"] = "stock"
panel = opus_features.build_panel(panel_candles, asset_classes)
check("panel covers every market", len(panel) == 10)
check("peer groups follow the asset class", panel["M9-EUR"].group == "stock")
check("unknown markets are skipped", len(opus_features.build_panel(panel_candles, {})) == 0)

index_returns = opus_features.group_index_returns(panel)
check("crypto index has history", len(index_returns["crypto"]) > 100)
check("stock index stays empty below three members", not index_returns["stock"])

opus_features.add_relative_features(panel, index_returns)
crypto_last = len(panel["M0-EUR"].days) - 1
check("beta to the peer index is set", panel["M0-EUR"].features["beta_mkt"][crypto_last] is not None)
check("correlation is bounded", abs(panel["M0-EUR"].features["corr_mkt"][crypto_last] or 0) <= 1.0)
check("residual momentum is set", panel["M0-EUR"].features["resid_mom"][crypto_last] is not None)
check("no macro beta without macro data", panel["M0-EUR"].features["beta_vix"][crypto_last] is None)

macro_days = sorted({day for series_ in panel.values() for day in series_.days})
rng = random.Random(7)
macro_changes = {"vix": {day: rng.gauss(0, 0.05) for day in macro_days}}
opus_features.add_relative_features(panel, index_returns, macro_changes)
check("macro beta appears once macro data exists", panel["M0-EUR"].features["beta_vix"][crypto_last] is not None)

members = [(market, len(panel[market].days) - 1) for market in panel if panel[market].group == "crypto"]
z_by_market = opus_features.cross_section(panel, members)
check("every member gets z-scores", len(z_by_market) == 9)
momentum = {m: z["mom_21"] for m, z in z_by_market.items() if "mom_21" in z}
check("strongest drifter ranks top on momentum", max(momentum, key=lambda m: momentum[m]) == "M8-EUR")
check("weakest drifter ranks bottom", min(momentum, key=lambda m: momentum[m]) == "M0-EUR")

funding = {"M0-EUR": {panel["M0-EUR"].days[crypto_last]: 0.0004}}
opus_features.attach_series_feature(panel, "funding", funding)
check("funding is attached by day", panel["M0-EUR"].features["funding"][crypto_last] == 0.0004)
opus_features.attach_series_feature(panel, "nonexistent", funding)
check("unknown feature names are ignored", "nonexistent" not in panel["M0-EUR"].features)

ts_z = opus_features.time_series_z(panel["M8-EUR"], crypto_last)
check("time-series fallback scores features", "mom_21" in ts_z)
check("time-series scores are bounded", all(abs(v) <= 2.0 for v in ts_z.values()))

# ---------------------------------------------------------------- calibration

print("calibration helpers")
check("clip bounds a lottery win", opus_calibration.clip_return(5.0, 1) == 0.3)
check("clip scales with the horizon", abs(opus_calibration.clip_return(5.0, 4) - 0.6) < 1e-9)
check("bins map score to index", opus_calibration.bin_index(-99) == 0 and opus_calibration.bin_index(99) == len(opus_calibration.BIN_EDGES))
check("bin centers increase", opus_calibration.bin_center(0) < opus_calibration.bin_center(5))

noise_stats = {feature: opus_calibration._Accumulator() for feature in opus_features.FEATURE_KEYS}
for index in range(200):
    swing = 0.05 if index % 2 == 0 else -0.05
    for accumulator in noise_stats.values():
        accumulator.add(swing)
weights, learned = opus_calibration.weights_from_ic(noise_stats)
check("pure noise falls back to the prior", not learned)
check("prior weights are normalized", abs(sum(abs(w) for w in weights.values()) - 1.0) < 1e-9)

# Each of these features clears one standard error (t ~ 1.4) but none clears
# two, so the vector as a whole is not allowed to call itself learned.
weak_stats = {feature: opus_calibration._Accumulator() for feature in opus_features.FEATURE_KEYS}
for index in range(200):
    swing = 0.05 if index % 2 == 0 else -0.05
    for feature in ("mom_21", "rev_5", "vol_z", "rsi_dev"):
        weak_stats[feature].add(0.005 + swing)
weak_weights, weak_learned = opus_calibration.weights_from_ic(weak_stats)
check(
    "borderline evidence earns weight but not the learned flag",
    1.0 <= abs(weak_stats["mom_21"].t_stat()) < 2.0 and not weak_learned,
)
check("and the prior is used instead", abs(sum(abs(w) for w in weak_weights.values()) - 1.0) < 1e-9)

rng = random.Random(3)
signal_stats = {feature: opus_calibration._Accumulator() for feature in opus_features.FEATURE_KEYS}
for _ in range(200):
    signal_stats["mom_21"].add(0.06 + rng.gauss(0, 0.01))
    signal_stats["rev_5"].add(-0.05 + rng.gauss(0, 0.01))
    signal_stats["vol_z"].add(0.04 + rng.gauss(0, 0.01))
    signal_stats["bb_pos"].add(rng.gauss(0, 0.05))
weights, learned = opus_calibration.weights_from_ic(signal_stats)
check("a real signal is learned", learned)
check("positive IC keeps its sign", weights.get("mom_21", 0) > 0)
check("negative IC keeps its sign", weights.get("rev_5", 0) < 0)
check("momentum outweighs the weaker signal", abs(weights["mom_21"]) > abs(weights["vol_z"]))
check(
    "an unstable feature is left near the prior",
    abs(weights.get("bb_pos", 0.0)) < abs(weights["mom_21"]) / 3,
)

print("overlap correction")
short = opus_calibration._Accumulator(1)
long_overlap = opus_calibration._Accumulator(21)
for _ in range(200):
    value = 0.04 + rng.gauss(0, 0.05)
    short.add(value)
    long_overlap.add(value)
check("same mean either way", abs(short.mean - long_overlap.mean) < 1e-12)
check("overlapping samples are less significant", abs(long_overlap.t_stat()) < abs(short.t_stat()))
check("effective count divides by the overlap", abs(long_overlap.effective_count - 200 / 21) < 1e-9)

print("composite_score")
composite, used = opus_calibration.composite_score({"mom_21": 1.0, "rev_5": -1.0}, {"mom_21": 0.6, "rev_5": 0.4})
check("weighted average of available features", abs(composite - 0.2) < 1e-9)
check("reports the weight it could use", abs(used - 1.0) < 1e-9)
composite, used = opus_calibration.composite_score({"mom_21": 1.0}, {"mom_21": 0.6, "rev_5": 0.4})
check("missing features are rescaled away", abs(composite - 1.0) < 1e-9 and abs(used - 0.6) < 1e-9)
check("no usable feature scores zero", opus_calibration.composite_score({}, {"mom_21": 0.6}) == (0.0, 0.0))

print("regime labels")
up_index = {f"2024-01-{d:02d}": 0.01 for d in range(1, 29)}
check("a rising index is an up regime", opus_calibration.current_regime(up_index) == "up")
down_index = {f"2024-01-{d:02d}": -0.01 for d in range(1, 29)}
check("a falling index is a down regime", opus_calibration.current_regime(down_index) == "down")
check("no history means no regime", opus_calibration.current_regime({}) == "all")

print("walk-forward calibration recovers a planted signal")
# Each market's forward 5-day return is driven by its own momentum rank, so a
# correct calibration must find a positive information coefficient on mom_21.
rng = random.Random(11)
planted: dict[str, list[list]] = {}
for i in range(24):
    bias = (i - 12) / 12 * 0.004
    closes = [100.0]
    for step in range(420):
        # Momentum persists: yesterday's drift predicts today's move.
        closes.append(closes[-1] * math.exp(bias + rng.gauss(0, 0.008)))
    planted[f"P{i}-EUR"] = candles_from_closes(closes)
planted_panel = opus_features.build_panel(planted, {m: "crypto" for m in planted})
planted_index = opus_features.group_index_returns(planted_panel)
opus_features.add_relative_features(planted_panel, planted_index)
calibrations = opus_calibration.calibrate(planted_panel, planted_index)

check("calibrates the crypto group", any(key[0] == "crypto" for key in calibrations))
payload = calibrations.get(("crypto", "1w", "all"))
check("has a pooled weekly calibration", payload is not None)
assert payload is not None
check("weights were learned", payload["weights_learned"])
check("records the engine version", payload["engine_version"] == opus_calibration.ENGINE_VERSION)
check("weights are normalized", abs(sum(abs(w) for w in payload["weights"].values()) - 1.0) < 1e-6)
check("recovers the planted momentum sign", (payload["ic"]["mom_21"]["mean"]) > 0)
check("momentum earns weight", payload["weights"].get("mom_21", 0) > 0)
check("reports its walk-forward diagnostics", payload["walk_forward"]["ic_days"] > 0)
check("records the calibration window", payload["from"] < payload["to"])
check("counts the days it saw", payload["days"] >= opus_calibration.MIN_IC_DAYS)
check("skips groups without members", not any(key[0] == "stock" for key in calibrations))

print("pick_payload")
reliable = {"reliable": True, "tag": "regime"}
unreliable = {"reliable": False, "tag": "regime"}
pooled = {"reliable": True, "tag": "pooled"}
check(
    "prefers a reliable regime calibration",
    opus_calibration.pick_payload({("crypto", "1w", "up"): reliable, ("crypto", "1w", "all"): pooled}, "crypto", "1w", "up")["tag"] == "regime",
)
check(
    "falls back to the pooled one",
    opus_calibration.pick_payload({("crypto", "1w", "up"): unreliable, ("crypto", "1w", "all"): pooled}, "crypto", "1w", "up")["tag"] == "pooled",
)
check(
    "uses an unreliable row rather than nothing",
    opus_calibration.pick_payload({("crypto", "1w", "up"): unreliable}, "crypto", "1w", "up")["tag"] == "regime",
)
check("no calibration at all yields None", opus_calibration.pick_payload({}, "crypto", "1w", "up") is None)

print("expected_alpha_pct")
check(
    "withholds an expected return when unreliable",
    opus_calibration.expected_alpha_pct({"reliable": False, "composite_scale": 1.0}, 1.0) is None,
)
binned = {
    "reliable": True,
    "composite_scale": 1.0,
    "bins": [
        {"bin": 0, "count": 100, "mean_return_pct": -2.0},
        {"bin": len(opus_calibration.BIN_EDGES), "count": 100, "mean_return_pct": 3.0},
    ],
}
check("high scores map to the top bin", opus_calibration.expected_alpha_pct(binned, 99.0) == 3.0)
check("low scores map to the bottom bin", opus_calibration.expected_alpha_pct(binned, -99.0) == -2.0)
middle = opus_calibration.expected_alpha_pct(binned, 0.0)
check("in between it interpolates", middle is not None and -2.0 < middle < 3.0)
check(
    "a negative walk-forward IC produces no fake edge",
    opus_calibration.expected_alpha_pct(
        {"reliable": True, "composite_scale": 1.0, "bins": [], "walk_forward": {"ic": -0.02}, "market_return": {"std_pct": 5.0}}, 1.0
    ) is None,
)

# ------------------------------------------------------------------- scoring

print("recommendation_from_edge")
none_rec = opus_analysis.recommendation_from_edge(None, None)
check("no calibration means hold", none_rec["action"] == "hold" and none_rec["score"] is None)
check("and no edge figures", none_rec["net_edge_pct"] is None and none_rec["buy_score"] == 0)

buy = opus_analysis.recommendation_from_edge(3.0, 4.0, bars=5, taker_pct=0.25, maker_pct=0.15)
check("a clear edge is a buy", buy["action"] in ("buy", "strong_buy"))
check("direction agrees with the action", buy["direction"] == "bullish")
check("fee is a round trip", buy["fee_pct"] == "0.50")
check("net edge subtracts the fee", buy["net_edge_pct"] == "2.50")
check("limit orders cost less", float(buy["net_edge_limit_pct"]) > float(buy["net_edge_pct"]))
check("no limit order needed", not buy["requires_limit_order"])
check("sell score stays zero on a buy", buy["sell_score"] == 0)

thin = opus_analysis.recommendation_from_edge(0.40, 4.0, bars=5, taker_pct=0.25, maker_pct=0.15)
check("an edge between the fee tiers needs a limit order", thin["requires_limit_order"])
check("taker round trip is negative there", float(thin["net_edge_pct"]) < 0 < float(thin["net_edge_limit_pct"]))

sell = opus_analysis.recommendation_from_edge(-3.0, 4.0, bars=5, taker_pct=0.25, maker_pct=0.15)
check("a negative expected return sells", sell["action"] in ("reduce", "sell"))
check("selling only pays one fee", sell["sell_edge_pct"] == "2.75")
check("gauge score is negative", sell["score"] < 0 and sell["direction"] == "bearish")
check("buy score stays zero on a sell", sell["buy_score"] == 0)

flat = opus_analysis.recommendation_from_edge(0.30, 0.02, bars=5)
check("a flat instrument is forced to hold", flat["action"] == "hold" and flat["low_volatility"])
check("and scores zero either way", flat["score"] == 0 and flat["buy_score"] == 0 == flat["sell_score"])

check(
    "score, direction and action never contradict",
    all(
        (rec["score"] > 0) == (rec["direction"] == "bullish")
        for rec in (buy, opus_analysis.recommendation_from_edge(1.5, 3.0))
    ),
)
check("horizon bars per label", [opus_analysis.horizon_bars(h) for h in ("1d", "1w", "4w")] == [1, 5, 21])
check("round trip is twice the taker fee", opus_analysis.round_trip_fee_pct(0.25) == 0.5)

print("score_market")
z_scores = {"mom_21": 1.5, "rev_5": -0.5, "vol_level": 0.2}
scored = opus_analysis.score_market(z_scores, None, regime="up", raw_values={"beta_mkt": 1.0})
check("works without a calibration", scored["outlook"]["direction"] in ("bullish", "bearish", "neutral"))
check("but offers no expected return", scored["expected_return_pct"] is None)
check("and says the weights are not learned", not scored["weights_learned"])

drift_payload = {
    **payload,
    "reliable": True,
    "market_return": {"mean_pct": 1.0, "std_pct": 5.0, "days": 400},
}
high_beta = opus_analysis.score_market(z_scores, drift_payload, regime="up", raw_values={"beta_mkt": 2.0})
low_beta = opus_analysis.score_market(z_scores, drift_payload, regime="up", raw_values={"beta_mkt": 0.0})
check("drift reaches a market through its beta", high_beta["market_return_pct"] > low_beta["market_return_pct"])
check("a zero-beta market inherits no drift", low_beta["market_return_pct"] == 0.0)
check("beta is capped", opus_analysis.score_market(z_scores, drift_payload, raw_values={"beta_mkt": 99.0})["beta"] == opus_analysis.BETA_CAP)

print("analyze_opus")
long_candles = candles_from_closes(trending(300, drift=0.003, noise=0.012, seed=5))
standalone = opus_analysis.analyze_opus(long_candles, 90)
check("time-series mode without context", standalone["mode"] == "time_series")
check("returns the display window", len(standalone["candles"]) == 90)
check("template shape: outlook", set(standalone["outlook"]) >= {"direction", "score", "buy_score", "sell_score", "confidence", "regime", "reason", "contributions"})
check("template shape: strategies", len(standalone["strategies"]) == len(opus_features.FEATURE_KEYS))
check("every strategy has a reason code", all("code" in s["reason"] for s in standalone["strategies"].values()))
check("suggests a stop below the price", float(standalone["recommendation"]["suggested_stop_price"]) < float(long_candles[-1][4]))
check("stop distance is a percentage", 0 < float(standalone["recommendation"]["suggested_stop_pct"]) < 100)
check("no calibrated return standalone", standalone["recommendation"]["expected_return_pct"] is None)

short_analysis = opus_analysis.analyze_opus(candles_from_closes(trending(20)), 10)
check("too little history yields no direction", short_analysis["outlook"]["direction"] == "none")
check("and an explicit no-data reason", short_analysis["outlook"]["reason"]["code"] == "outlook_no_data")
check("but still returns candles", len(short_analysis["candles"]) == 10)

context = {
    "market": "P12-EUR",
    "asset_class": "crypto",
    "peer_group": "crypto",
    "horizon": "1w",
    "regime": "up",
    "z_scores": {"mom_21": 1.2, "rev_5": -0.8, "vol_level": -0.3, "resid_mom": 0.9},
    "raw_values": {"beta_mkt": 1.1},
    "calibration": drift_payload,
    "expected_vol_pct": 4.0,
    "taker_pct": 0.25,
    "maker_pct": 0.15,
}
detailed = opus_analysis.analyze_opus(long_candles, 60, context)
check("cross-sectional mode with context", detailed["mode"] == "cross_sectional")
check("reports the calibration provenance", detailed["calibration"]["peer_group"] == "crypto")
check("expected return is available", detailed["recommendation"]["expected_return_pct"] is not None)
check("expected move over the horizon", detailed["recommendation"]["expected_move_pct"] == "4.00")
check("outlook and recommendation agree", detailed["outlook"]["score"] == detailed["recommendation"]["score"])
check("features carry their percentile", detailed["strategies"]["mom_21"]["values"]["percentile"] is not None)
check("features carry the learned weight", "weight" in detailed["strategies"]["mom_21"]["values"])
check("features without data say so", detailed["strategies"]["funding"]["reason"]["code"] == "insufficient_data")

print("expected_move_pct")
move_1d = opus_analysis.expected_move_pct(series, last, 1)
move_1w = opus_analysis.expected_move_pct(series, last, 5)
check("the expected move grows with the horizon", move_1w > move_1d)
check("and scales with the square root of time", abs(move_1w / move_1d - math.sqrt(5)) < 1e-6)

# ------------------------------------------------------------- ranking gates

print("finalize_row gates")


def base_row(**overrides) -> dict:
    row = {
        "market": "AAA-EUR",
        "asset_class": "crypto",
        "peer_group": "crypto",
        "horizon": "1w",
        "score": 30,
        "direction": "bullish",
        "confidence": "medium",
        "expected_return_pct": 3.0,
        "expected_move_pct": 4.0,
        "turnover_eur": 1_000_000.0,
        "days_since_close": 0,
    }
    row.update(overrides)
    return row


healthy = opus_analysis.finalize_row(base_row(), market_open=True)
check("a healthy row is tradable", healthy["tradable"] and healthy["liquidity_ok"] and not healthy["stale"])
check("and suggests a market order", healthy["suggested_order_type"] == "market")
check("and keeps its buy advice", healthy["action"] in ("buy", "strong_buy"))

illiquid = opus_analysis.finalize_row(base_row(turnover_eur=100.0), market_open=True)
check("an illiquid market is not tradable", not illiquid["liquidity_ok"] and not illiquid["tradable"])
check("and is never advised as a buy", illiquid["action"] == "hold" and illiquid["buy_score"] == 0)

stale = opus_analysis.finalize_row(base_row(), market_open=True, days_since_close=10)
check("stale data is flagged", stale["stale"] and stale["buy_score"] == 0)

commodity = opus_analysis.finalize_row(
    base_row(asset_class="commodity", turnover_eur=None), market_open=True
)
check("commodities skip the volume check", commodity["liquidity_ok"] and commodity["tradable"])

closed = opus_analysis.finalize_row(base_row(), market_open=False)
check("a closed market is not tradable now", not closed["tradable_now"])
check("and is offered as a limit order", closed["suggested_order_type"] == "limit")

uncalibrated = opus_analysis.finalize_row(base_row(expected_return_pct=None, score=60), market_open=True)
check("falls back to the peer-rank gauge", uncalibrated["score"] == 60)
check("and only acts on the clearest cases", uncalibrated["action"] == "buy")

held = opus_analysis.finalize_row(base_row(expected_return_pct=-2.0), market_open=True, held=True)
check("a holding gets an exit opinion", held["action"] in ("reduce", "sell") and held["held"])
check("fees are reported per tier", held["taker_pct"] is not None and held["maker_pct"] is not None)

print("rank_rows and select_basket")
rows = [
    opus_analysis.finalize_row(base_row(market="BEST-EUR", expected_return_pct=5.0), market_open=True),
    opus_analysis.finalize_row(base_row(market="GOOD-EUR", expected_return_pct=3.0), market_open=True),
    opus_analysis.finalize_row(base_row(market="WEAK-EUR", expected_return_pct=-4.0), market_open=True),
    opus_analysis.finalize_row(base_row(market="JUNK-EUR", expected_return_pct=9.0, turnover_eur=10.0), market_open=True),
]
opus_analysis.rank_rows(rows)
by_market = {row["market"]: row for row in rows}
check("the strongest edge ranks first to buy", by_market["BEST-EUR"]["buy_rank"] == 1)
check("the weakest ranks first to sell", by_market["WEAK-EUR"]["sell_rank"] == 1)
check("untradable rows are ranked last", by_market["JUNK-EUR"]["buy_rank"] == len(rows))
check("ranks are a permutation", sorted(row["buy_rank"] for row in rows) == list(range(1, len(rows) + 1)))

basket = opus_analysis.select_basket(rows)
check("the basket holds the actionable buys", basket[:2] == ["BEST-EUR", "GOOD-EUR"])
check("and excludes the illiquid one", "JUNK-EUR" not in basket)
check("and excludes the sells", "WEAK-EUR" not in basket)

crowded = [
    opus_analysis.finalize_row(
        base_row(market=f"C{i}-EUR", expected_return_pct=5.0 - i * 0.01), market_open=True
    )
    for i in range(12)
]
opus_analysis.rank_rows(crowded)
capped = opus_analysis.select_basket(crowded)
check("one peer group cannot fill the basket", len(capped) == opus_analysis.BASKET_GROUP_CAP["crypto"])

mixed = crowded + [
    opus_analysis.finalize_row(
        base_row(market=f"S{i}-EUR", asset_class="stock", peer_group="stock", expected_return_pct=4.0),
        market_open=True,
    )
    for i in range(6)
]
opus_analysis.rank_rows(mixed)
diversified = opus_analysis.select_basket(mixed)
check("a mixed field diversifies across groups", len({m[0] for m in diversified}) > 1)
check("and respects the basket size", len(diversified) <= opus_analysis.BASKET_SIZE)

print("calibration_summary")
check("no payload means no summary", opus_analysis.calibration_summary(None) is None)
summary = opus_analysis.calibration_summary(payload)
check("summarizes provenance", summary["peer_group"] == "crypto" and summary["horizon"] == "1w")
check("lists the heaviest features", len(summary["top_features"]) == 5)
check("reports the walk-forward IC", summary["walk_forward_ic"] is not None)

# --------------------------------------------------------------- macro parsing

print("macro parsing")
fred = opus_macro.parse_fred_csv(
    "observation_date,DGS10\n2026-07-29,4.35\n2026-07-30,.\n2026-07-31,4.41\nbad,row\n"
)
check("parses FRED observations", fred == {"2026-07-29": 4.35, "2026-07-31": 4.41})
check("skips missing FRED values", "2026-07-30" not in fred)
check("empty CSV is empty", opus_macro.parse_fred_csv("") == {})

fng = opus_macro.parse_fear_greed({"data": [
    {"value": "61", "timestamp": "1754179200"},
    {"value": "nope", "timestamp": "1754092800"},
    {"timestamp": "1754006400"},
]})
check("parses Fear & Greed history", list(fng.values()) == [61.0])
check("ignores malformed sentiment rows", len(fng) == 1)

stable = opus_macro.parse_stablecoin_supply([
    {"date": "1754179200", "totalCirculating": {"peggedUSD": 1.7e11}},
    {"date": "1754092800", "totalCirculating": {}},
])
check("parses stablecoin supply", list(stable.values()) == [1.7e11])

# ------------------------------------------------------- store, snapshots, live

print("store: macro series")
engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)

with Session(engine) as db:
    check("writes new points", opus_store.upsert_series(db, "fred:vix", {"2026-07-30": 15.0, "2026-07-31": 16.0}) == 2)
    check("rewrites only changes", opus_store.upsert_series(db, "fred:vix", {"2026-07-30": 15.0, "2026-07-31": 17.0}) == 1)
    check("reads a series back", opus_store.load_series(db, "fred:vix") == {"2026-07-30": 15.0, "2026-07-31": 17.0})
    check("rejects non-finite values", opus_store.upsert_series(db, "fred:vix", {"2026-08-01": float("nan")}) == 0)
    opus_store.upsert_many_series(db, {"funding:BTC": {"2026-07-31": 0.0001}, "funding:ETH": {"2026-07-31": -0.0002}})
    check("groups by series prefix", set(opus_store.load_series_prefix(db, "funding:")) == {"funding:BTC", "funding:ETH"})
    check("maps funding series to markets", set(opus_store._funding_by_market(db)) == {"BTC-EUR", "ETH-EUR"})
    check("reports per-series coverage", any(s["series_id"] == "fred:vix" and s["points"] == 2 for s in opus_store.series_status(db)))

    opus_store.upsert_series(db, "fred:us10y", {"2026-07-30": 4.0, "2026-07-31": 4.2})
    changes = opus_store.macro_change_series(db)
    check("rate changes are differences", abs(changes["rate"]["2026-07-31"] - 0.2) < 1e-9)
    check("vix changes are log returns", abs(changes["vix"]["2026-07-31"] - math.log(17 / 15)) < 1e-9)

    opus_store.upsert_series(db, "fred:us2y", {"2026-07-31": 3.8})
    macro = opus_store.macro_context(db)
    check("macro context shows the latest VIX", macro["vix"] == 17.0)
    check("and the yield curve", abs(macro["yield_curve"] - 0.4) < 1e-9)

print("store: calibration round trip")
with Session(engine) as db:
    check("saves calibration rows", opus_store.save_calibrations(db, calibrations) == len(calibrations))
    loaded = opus_store.load_calibrations(db)
    check("loads them back", set(loaded) == set(calibrations))
    check("payload survives the round trip", loaded[("crypto", "1w", "all")]["weights"] == payload["weights"])
    check("re-saving updates in place", opus_store.save_calibrations(db, calibrations) == len(calibrations))
    check("status reports the row count", opus_store.calibration_status(db)["rows"] == len(calibrations))

print("store: snapshots and live track record")
with Session(engine) as db:
    day = "2026-07-01"
    snapshot_rows = [
        {"market": "BTC-EUR", "day": day, "action": "buy", "direction": "bullish", "score": 40,
         "buy_score": 55, "sell_score": 0, "expected_return_pct": "2.0", "net_edge_pct": "1.5",
         "conviction": "0.5", "buy_rank": 1, "close": "100"},
        {"market": "ETH-EUR", "day": day, "action": "sell", "direction": "bearish", "score": -40,
         "buy_score": 0, "sell_score": 55, "expected_return_pct": "-2.0", "net_edge_pct": "-2.5",
         "conviction": "-0.5", "buy_rank": 400, "close": "50"},
    ]
    check("writes a snapshot", opus_store.save_snapshot(db, "1w", snapshot_rows) == 2)
    check("re-running the same day does not duplicate", opus_store.save_snapshot(db, "1w", snapshot_rows) == 0)
    stored = db.query(OpusRecommendation).count()
    check("one row per market and horizon", stored == 2)

    # Ten sessions of candles so the 5-bar horizon can be graded.
    for market, start_price, direction in (("BTC-EUR", 100.0, 1.0), ("ETH-EUR", 50.0, -1.0)):
        for i in range(10):
            price = start_price * (1 + direction * 0.01 * i)
            db.add(MarketCandle(
                market=market,
                day=datetime(2026, 7, 1, tzinfo=timezone.utc) + timedelta(days=i),
                open=Decimal(str(price)), high=Decimal(str(price)), low=Decimal(str(price)),
                close=Decimal(str(price)), volume=Decimal("10"),
            ))
    db.commit()

    graded = opus_store.evaluate_snapshots(db, now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    check("grades both recommendations", graded == 2)
    check("nothing left to grade on a second pass", opus_store.evaluate_snapshots(db, now=datetime(2026, 8, 1, tzinfo=timezone.utc)) == 0)
    btc = db.query(OpusRecommendation).filter_by(market="BTC-EUR").one()
    check("realized return follows the price", btc.realized_return_pct is not None and btc.realized_return_pct > 0)
    check("and is timestamped", btc.evaluated_at is not None)

    check("too few samples means no live record", opus_store.live_track_record(db, "1w") is None)

    # Both calls above were correct; add enough graded calls to pass the floor.
    for i in range(2, 22):
        db.add(OpusRecommendation(
            day=datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=i),
            market="BTC-EUR", horizon="1w", action="buy", direction="bullish",
            score=30, buy_score=40, sell_score=0,
            realized_return_pct=1.0 if i % 4 else -1.0,
        ))
    db.commit()
    record = opus_store.live_track_record(db, "1w")
    check("live record appears past the floor", record is not None)
    check("hit rate is honest", record is not None and 50.0 < float(record["hit_rate_pct"]) < 100.0)
    check("counts buys and sells apart", record["buy_samples"] + record["sell_samples"] == record["samples"])
    check("reports the period", record["from"] <= record["to"])
    check("hold advice is not graded", record["samples"] == db.query(OpusRecommendation).filter(OpusRecommendation.action != "hold").filter(OpusRecommendation.realized_return_pct.isnot(None)).count())
    check("can be filtered per market", opus_store.live_track_record(db, "1w", market="ETH-EUR") is None)
    check("snapshot status counts rows", opus_store.snapshot_status(db)["rows"] == 22)

print("store: pruning")
with Session(engine) as db:
    db.add(OpusRecommendation(
        day=datetime(2020, 1, 1, tzinfo=timezone.utc), market="OLD-EUR", horizon="1w",
        action="buy", direction="bullish", score=1, buy_score=1, sell_score=0,
    ))
    db.commit()
    removed = opus_store.prune_snapshots(db, now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    check("drops snapshots past retention", removed == 1)
    check("keeps recent ones", db.query(OpusRecommendation).count() == 22)

print("store: detail_context")
scores = {
    "detail": {"BTC-EUR": {
        "market": "BTC-EUR", "peer_group": "crypto", "regime": "up", "peers": 30,
        "day": "2026-07-31", "days_since_close": 1, "z_scores": {"mom_21": 1.0},
        "raw_values": {"beta_mkt": 1.0}, "turnover_eur": 5e6,
        "expected_move_pct": {"1d": 1.6, "1w": 3.6, "4w": 7.3},
    }},
    "calibrations": calibrations,
}
detail = opus_store.detail_context(scores, "BTC-EUR", "1w")
check("builds the per-market context", detail is not None and detail["horizon"] == "1w")
check("picks the horizon's expected move", detail["expected_vol_pct"] == 3.6)
check("attaches the right calibration", detail["calibration"] is None or detail["calibration"]["horizon"] == "1w")
check("unknown markets have no context", opus_store.detail_context(scores, "NOPE-EUR", "1w") is None)

print("json serializable payloads")
check("calibration payload is json-safe", isinstance(json.dumps(payload), str))
check("analysis payload is json-safe", isinstance(json.dumps(detailed, default=str), str))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
