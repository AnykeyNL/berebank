"""Standalone verification of the KimiK3 outlook engine and track record.

Run: .venv\\Scripts\\python test_kimi_analysis.py
"""
import math

from app.services import backtest, kimi_analysis

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


print("ADX")
closes = [100.0 + i for i in range(60)]
highs = [c + 1 for c in closes]
lows = [c - 1 for c in closes]
a, p, m = kimi_analysis.adx(highs, lows, closes)
check("undefined before 2*period-1", all(v is None for v in a[:27]))
check("defined from 2*period-1", a[27] is not None)
check("steady uptrend: strong ADX, +DI dominates", a[-1] > 25 and p[-1] > m[-1])

closes_down = [200.0 - i for i in range(60)]
a_d, p_d, m_d = kimi_analysis.adx(
    [c + 1 for c in closes_down], [c - 1 for c in closes_down], closes_down
)
check("steady downtrend: strong ADX, -DI dominates", a_d[-1] > 25 and m_d[-1] > p_d[-1])

zigzag = [100.0 + (1.0 if i % 2 else -1.0) for i in range(60)]
a_z, _, _ = kimi_analysis.adx([c + 1 for c in zigzag], [c - 1 for c in zigzag], zigzag)
check("zigzag: ranging ADX", a_z[-1] is not None and a_z[-1] < 20, f"got {a_z[-1]}")

check("too few bars -> all None", all(v is None for v in kimi_analysis.adx([1, 2], [1, 2], [1, 2])[0]))

print("regime_for")
check("trending at 25", kimi_analysis.regime_for(25.0) == "trending")
check("ranging below 20", kimi_analysis.regime_for(19.9) == "ranging")
check("neutral between", kimi_analysis.regime_for(22.0) == "neutral")
check("neutral without ADX", kimi_analysis.regime_for(None) == "neutral")

print("compute_outlook")
all_up = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER})
out = kimi_analysis.compute_outlook(all_up)
check("all bullish -> bullish 100 high", out["direction"] == "bullish" and out["score"] == 100 and out["confidence"] == "high")

all_down = strategies({k: "bearish" for k in kimi_analysis.STRATEGY_ORDER})
out = kimi_analysis.compute_outlook(all_down)
check("all bearish -> bearish -100", out["direction"] == "bearish" and out["score"] == -100)

mixed = strategies({
    "trend": "bullish", "rsi": "bullish", "macd": "bullish",
    "volatility": "bearish", "levels_volume": "bearish", "trend_strength": "bearish",
})
out = kimi_analysis.compute_outlook(mixed)
check("split vote -> neutral score 0", out["direction"] == "neutral" and out["score"] == 0)
check("split vote -> low confidence", out["confidence"] == "low")

none_all = strategies({k: "none" for k in kimi_analysis.STRATEGY_ORDER})
out = kimi_analysis.compute_outlook(none_all)
check("no data -> direction none", out["direction"] == "none" and out["reason"]["code"] == "outlook_no_data")

trending = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=30.0)
out = kimi_analysis.compute_outlook(trending)
weights = {c["strategy"]: c["weight"] for c in out["contributions"]}
check("trending regime doubles trend+macd", weights["trend"] == 2.0 and weights["macd"] == 2.0)
check("trending regime leaves others at 1", weights["rsi"] == 1.0 and weights["volatility"] == 1.0)
check("regime reported", out["regime"] == "trending")

ranging = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=10.0)
out = kimi_analysis.compute_outlook(ranging)
weights = {c["strategy"]: c["weight"] for c in out["contributions"]}
check("ranging regime doubles rsi+volatility", weights["rsi"] == 2.0 and weights["volatility"] == 2.0)
check("ranging regime leaves trend at 1", weights["trend"] == 1.0)

# Score exactly at the +20 threshold: votes +1,+1,0,0,-1 over 5 active -> 20.
edge = strategies({
    "trend": "bullish", "rsi": "bullish", "macd": "neutral",
    "volatility": "neutral", "levels_volume": "bearish", "trend_strength": "none",
})
out = kimi_analysis.compute_outlook(edge)
check("score 20 is bullish", out["score"] == 20 and out["direction"] == "bullish", f"got {out['score']}")

# "none" strategies are excluded from the vote entirely.
partial = strategies({
    "trend": "bullish", "rsi": "none", "macd": "none",
    "volatility": "none", "levels_volume": "none", "trend_strength": "none",
})
out = kimi_analysis.compute_outlook(partial)
check("single active strategy decides", out["direction"] == "bullish" and out["score"] == 100)
check("confidence high when 1/1 agrees", out["confidence"] == "high")

print("supplementary context")
ranging = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=10.0)
out_plain = kimi_analysis.compute_outlook(ranging)
out_earnings = kimi_analysis.compute_outlook(ranging, {"earnings_near": True})
w_plain = {c["strategy"]: c["weight"] for c in out_plain["contributions"]}
w_earnings = {c["strategy"]: c["weight"] for c in out_earnings["contributions"]}
check("earnings_near suppresses rsi doubling", w_plain["rsi"] == 2.0 and w_earnings["rsi"] == 1.0)

neutral = strategies({
    "trend": "bullish", "rsi": "bearish", "macd": "neutral",
    "volatility": "neutral", "levels_volume": "neutral", "trend_strength": "neutral",
})
out_macro = kimi_analysis.compute_outlook(neutral, {"macro_regime": "risk_off"})
check("risk_off nudges neutral score down", out_macro["score"] <= -5)

tie = strategies({
    "trend": "bullish", "rsi": "bearish", "macd": "neutral",
    "volatility": "neutral", "levels_volume": "neutral", "trend_strength": "neutral",
})
out_insider = kimi_analysis.compute_outlook(tie, {"insider_signal": "bullish"})
check("insider tie-breaker nudges score up", out_insider["score"] > kimi_analysis.compute_outlook(tie)["score"])

print("analyze_kimi")
up_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(140)])
result = kimi_analysis.analyze_kimi(up_candles, 80)
check("six strategies", set(result["strategies"]) == set(kimi_analysis.STRATEGY_ORDER))
check("uptrend -> bullish outlook", result["outlook"]["direction"] == "bullish")
check("trend_strength signal", result["strategies"]["trend_strength"]["signal"] == "bullish")
check("reason code matches direction", result["outlook"]["reason"]["code"] == "outlook_bullish")
check("candles trimmed to display window", len(result["candles"]) == 80)

short = kimi_analysis.analyze_kimi(make_candles([100.0] * 10), 10)
check("short series -> no outlook", short["outlook"]["direction"] == "none")

print("backtest.track_record")
trend_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(200)])
record = backtest.track_record(trend_candles, kimi_analysis.analyze_kimi)
check("uptrend has a track record", record is not None)
check("steady uptrend: every bullish call hits", record["hit_rate_pct"] == "100.0", f"got {record}")
check("samples counted", record["samples"] > 100)
check("forward days exposed", record["forward_days"] == backtest.FORWARD_DAYS)
check("period covered", record["from"] < record["to"])

down_candles = make_candles([300.0 * math.exp(-0.002 * i) for i in range(200)])
record_down = backtest.track_record(down_candles, kimi_analysis.analyze_kimi)
check("steady downtrend: bearish calls hit", record_down is not None and record_down["hit_rate_pct"] == "100.0")
check("bearish avg return negative", float(record_down["avg_bearish_return_pct"]) < 0)

check(
    "too little history -> None",
    backtest.track_record(make_candles([100.0] * 50), kimi_analysis.analyze_kimi) is None,
)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
