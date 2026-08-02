"""Standalone verification of the Fable5 outlook engine and track record.

Run: .venv\\Scripts\\python test_fable5_analysis.py
"""
import math

from app.services import backtest, fable5_analysis

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def make_candles(closes, start_ts=1_700_000_000_000):
    """Build API-shape daily candles from close prices."""
    n = len(closes)
    return [
        [
            start_ts + i * 86_400_000,
            str(closes[i]),
            str(closes[i] * 1.01),
            str(closes[i] * 0.99),
            str(closes[i]),
            "1000",
        ]
        for i in range(n)
    ]


def strat(signal: str) -> dict:
    return {"signal": signal, "reason": {"code": "x", "params": {}}, "explanation": "", "values": {}, "series": {}}


def strategies(signals: dict[str, str], adx_value: float | None = None) -> dict:
    """Fake strategy results; trend_strength carries the ADX regime value."""
    out = {key: strat(sig) for key, sig in signals.items()}
    ts = strat(signals.get("trend_strength", "none"))
    ts["values"] = {"adx": str(adx_value)} if adx_value is not None else {}
    out["trend_strength"] = ts
    return out


print("roc")
closes = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
r = fable5_analysis.roc(closes, 5)
check("undefined before period", all(v is None for v in r[:5]))
check("value correct", r[5] is not None and abs(r[5] - 10.0) < 1e-9, f"got {r[5]}")
check("zero base handled", fable5_analysis.roc([0.0, 1.0], 1)[1] is None)

print("stochastic")
up = [100.0 + i for i in range(40)]
k, d = fable5_analysis.stochastic([c + 1 for c in up], [c - 1 for c in up], up)
check("undefined before warmup", k[12] is None and d[14] is None)
check("uptrend: %K high", k[-1] is not None and k[-1] > 80, f"got {k[-1]}")
down = [200.0 - i for i in range(40)]
k_d, _ = fable5_analysis.stochastic([c + 1 for c in down], [c - 1 for c in down], down)
check("downtrend: %K low", k_d[-1] is not None and k_d[-1] < 20, f"got {k_d[-1]}")
flat = [100.0] * 40
k_f, d_f = fable5_analysis.stochastic(flat, flat, flat)
check("flat window: no division by zero", k_f[-1] is None and d_f[-1] is None)

print("adx")
closes_up = [100.0 + i for i in range(60)]
a, p, m = fable5_analysis.adx([c + 1 for c in closes_up], [c - 1 for c in closes_up], closes_up)
check("undefined before 2*period-1", all(v is None for v in a[:27]))
check("defined from 2*period-1", a[27] is not None)
check("steady uptrend: strong ADX, +DI dominates", a[-1] > 25 and p[-1] > m[-1])
closes_down = [200.0 - i for i in range(60)]
a_d, p_d, m_d = fable5_analysis.adx(
    [c + 1 for c in closes_down], [c - 1 for c in closes_down], closes_down
)
check("steady downtrend: strong ADX, -DI dominates", a_d[-1] > 25 and m_d[-1] > p_d[-1])
zigzag = [100.0 + (1.0 if i % 2 else -1.0) for i in range(60)]
a_z, _, _ = fable5_analysis.adx([c + 1 for c in zigzag], [c - 1 for c in zigzag], zigzag)
check("zigzag: ranging ADX", a_z[-1] is not None and a_z[-1] < 20, f"got {a_z[-1]}")
check("too few bars -> all None", all(v is None for v in fable5_analysis.adx([1, 2], [1, 2], [1, 2])[0]))

print("regime_for")
check("trending at 25", fable5_analysis.regime_for(25.0) == "trending")
check("ranging below 20", fable5_analysis.regime_for(19.9) == "ranging")
check("neutral between", fable5_analysis.regime_for(22.0) == "neutral")
check("neutral without ADX", fable5_analysis.regime_for(None) == "neutral")

print("compute_outlook")
all_up = strategies({k: "bullish" for k in fable5_analysis.STRATEGY_ORDER})
out = fable5_analysis.compute_outlook(all_up)
check("all bullish -> bullish 100 high", out["direction"] == "bullish" and out["score"] == 100 and out["confidence"] == "high")

all_down = strategies({k: "bearish" for k in fable5_analysis.STRATEGY_ORDER})
out = fable5_analysis.compute_outlook(all_down)
check("all bearish -> bearish -100", out["direction"] == "bearish" and out["score"] == -100)

# Weights are fixed regardless of regime (unlike KimiK3).
trending = strategies({k: "bullish" for k in fable5_analysis.STRATEGY_ORDER}, adx_value=30.0)
out = fable5_analysis.compute_outlook(trending)
weights = {c["strategy"]: c["weight"] for c in out["contributions"]}
check("fixed weights match table", weights == fable5_analysis.WEIGHTS)
ranging = strategies({k: "bullish" for k in fable5_analysis.STRATEGY_ORDER}, adx_value=10.0)
out_r = fable5_analysis.compute_outlook(ranging)
weights_r = {c["strategy"]: c["weight"] for c in out_r["contributions"]}
check("weights unchanged in ranging regime", weights_r == fable5_analysis.WEIGHTS)
check("regime still reported", out["regime"] == "trending" and out_r["regime"] == "ranging")

# Weighted score: trend (2.0) bullish vs rsi+stochastic (1.0 each) bearish,
# rest none -> (2 - 2) / 4 = 0 -> neutral.
split = strategies({
    "trend": "bullish", "rsi": "bearish", "stochastic": "bearish",
    "macd": "none", "momentum": "none", "volatility": "none",
    "levels_volume": "none", "trend_strength": "none",
})
out = fable5_analysis.compute_outlook(split)
check("weighted split -> neutral score 0", out["direction"] == "neutral" and out["score"] == 0, f"got {out['score']}")
check("weighted split -> low confidence", out["confidence"] == "low")

# Weighted majority: trend (2.0) + macd (1.5) bullish vs rsi (1.0) bearish
# -> (3.5 - 1) / 4.5 = 55.6 -> bullish; agreement 3.5/4.5 = 0.78 -> high.
majority = strategies({
    "trend": "bullish", "macd": "bullish", "rsi": "bearish",
    "momentum": "none", "stochastic": "none", "volatility": "none",
    "levels_volume": "none", "trend_strength": "none",
})
out = fable5_analysis.compute_outlook(majority)
check("weighted majority -> bullish", out["direction"] == "bullish" and out["score"] == 56, f"got {out['score']}")
check("weighted agreement 78% -> high", out["confidence"] == "high")

# Neutral votes dilute confidence: trend bullish (2.0), five neutrals (5.5
# active weight of rsi+stoch+vol+levels+momentum... choose) -> direction from
# score, agreement measured against that direction.
diluted = strategies({
    "trend": "bullish", "macd": "neutral", "rsi": "neutral",
    "momentum": "bullish", "stochastic": "neutral", "volatility": "neutral",
    "levels_volume": "neutral", "trend_strength": "none",
})
out = fable5_analysis.compute_outlook(diluted)
# weighted = 2 + 1.5 = 3.5 of total 8.0 -> score 44 -> bullish; agreement 3.5/8 = 0.44 -> low.
check("neutral-heavy vote -> bullish but low confidence", out["direction"] == "bullish" and out["confidence"] == "low", f"got {out}")

none_all = strategies({k: "none" for k in fable5_analysis.STRATEGY_ORDER})
out = fable5_analysis.compute_outlook(none_all)
check("no data -> direction none", out["direction"] == "none" and out["reason"]["code"] == "outlook_no_data")

partial = strategies({
    "trend": "bullish", "macd": "none", "momentum": "none", "rsi": "none",
    "stochastic": "none", "volatility": "none", "levels_volume": "none",
    "trend_strength": "none",
})
out = fable5_analysis.compute_outlook(partial)
check("single active strategy decides", out["direction"] == "bullish" and out["score"] == 100)
check("confidence high when all active weight agrees", out["confidence"] == "high")

print("analyze_fable5")
up_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(140)])
result = fable5_analysis.analyze_fable5(up_candles, 80)
check("twelve strategies", set(result["strategies"]) == set(fable5_analysis.STRATEGY_ORDER))
check("uptrend -> bullish outlook", result["outlook"]["direction"] == "bullish")
check("momentum bullish in uptrend", result["strategies"]["momentum"]["signal"] == "bullish")
check("trend_strength bullish in uptrend", result["strategies"]["trend_strength"]["signal"] == "bullish")
check("stochastic overbought in steady uptrend", result["strategies"]["stochastic"]["signal"] == "bearish")
check("reason code matches direction", result["outlook"]["reason"]["code"] == "outlook_bullish")
check("candles trimmed to display window", len(result["candles"]) == 80)

down_result = fable5_analysis.analyze_fable5(
    make_candles([300.0 * math.exp(-0.002 * i) for i in range(140)]), 80
)
check("downtrend -> bearish outlook", down_result["outlook"]["direction"] == "bearish")
check("momentum bearish in downtrend", down_result["strategies"]["momentum"]["signal"] == "bearish")

short = fable5_analysis.analyze_fable5(make_candles([100.0] * 10), 10)
check("short series -> no outlook", short["outlook"]["direction"] == "none")

print("backtest.track_record")
trend_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(200)])
record = backtest.track_record(trend_candles, fable5_analysis.analyze_fable5)
check("uptrend has a track record", record is not None)
check("steady uptrend: every bullish call hits", record["hit_rate_pct"] == "100.0", f"got {record}")
check("samples counted", record["samples"] >= backtest.MIN_SAMPLES)
check("forward days exposed", record["forward_days"] == backtest.FORWARD_DAYS)
check("period covered", record["from"] < record["to"])

down_candles = make_candles([300.0 * math.exp(-0.002 * i) for i in range(200)])
record_down = backtest.track_record(down_candles, fable5_analysis.analyze_fable5)
check("steady downtrend: bearish calls hit", record_down is not None and record_down["hit_rate_pct"] == "100.0")
check("bearish avg return negative", float(record_down["avg_bearish_return_pct"]) < 0)

check(
    "too little history -> None",
    backtest.track_record(make_candles([100.0] * 50), fable5_analysis.analyze_fable5) is None,
)

print("macro strategies")
ctx_high = {"vix_level": 30.0, "us2y_yield": 4.0, "us10y_yield": 4.2, "yield_spread": 0.2}
ctx_low = {"vix_level": 12.0, "us2y_yield": 3.0, "us10y_yield": 4.0, "yield_spread": 1.0}
ctx_inv = {"vix_level": 18.0, "us2y_yield": 4.5, "us10y_yield": 4.0, "yield_spread": -0.5}
check("elevated VIX bearish", fable5_analysis._vix_regime(ctx_high)["signal"] == "bearish")
check("calm VIX bullish", fable5_analysis._vix_regime(ctx_low)["signal"] == "bullish")
check("inverted curve bearish", fable5_analysis._yield_curve(ctx_inv)["signal"] == "bearish")
check("steep curve bullish", fable5_analysis._yield_curve(ctx_low)["signal"] == "bullish")

print("crypto macro strategies")
ctx_fg_greed = {"context_type": "crypto", "fear_greed_index": 80}
ctx_fg_fear = {"context_type": "crypto", "fear_greed_index": 15}
check("extreme greed bearish", fable5_analysis._vix_regime(ctx_fg_greed)["signal"] == "bearish")
check("extreme fear bullish", fable5_analysis._vix_regime(ctx_fg_fear)["signal"] == "bullish")
ctx_liq = {
    "context_type": "crypto",
    "btc_dominance": 54.0,
    "btc_dominance_change_pct": -1.0,
    "stablecoin_supply_change_pct": 4.0,
}
check("supportive crypto liquidity", fable5_analysis._yield_curve(ctx_liq)["signal"] == "bullish")
result = fable5_analysis.analyze_fable5(
    make_candles([100.0 * math.exp(0.002 * i) for i in range(140)]), 80, ctx_fg_fear
)
check("twelve strategies with crypto context", len(result["strategies"]) == 12)

result = fable5_analysis.analyze_fable5(make_candles([100.0 * math.exp(0.002 * i) for i in range(140)]), 80, ctx_low)
check("twelve strategies with context", len(result["strategies"]) == 12)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
