"""Standalone verification of the technical-analysis service.

Run: .venv\\Scripts\\python test_analysis.py
"""
import math

from app.services import analysis

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def approx(a, b, tol=1e-9) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def make_candles(closes, highs=None, lows=None, volumes=None, start_ts=1_700_000_000_000):
    """Build API-shape candles from close prices (1h apart)."""
    n = len(closes)
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    volumes = volumes or [100.0] * n
    return [
        [start_ts + i * 3_600_000, str(closes[i]), str(highs[i]), str(lows[i]), str(closes[i]), str(volumes[i])]
        for i in range(n)
    ]


print("SMA")
values = [1.0, 2.0, 3.0, 4.0, 5.0]
out = analysis.sma(values, 3)
check("undefined before period", out[0] is None and out[1] is None)
check("first value", approx(out[2], 2.0))
check("rolls forward", approx(out[3], 3.0) and approx(out[4], 4.0))
check("period longer than data", analysis.sma([1.0, 2.0], 5) == [None, None])

print("EMA")
out = analysis.ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
check("seeded with SMA", approx(out[2], 2.0))
# k = 2/4 = 0.5: 2 + 0.5*(4-2) = 3; 3 + 0.5*(5-3) = 4
check("recursion", approx(out[3], 3.0) and approx(out[4], 4.0))

print("RSI")
rising = [float(i) for i in range(1, 20)]
out = analysis.rsi(rising, 14)
check("undefined before period+1", all(v is None for v in out[:14]))
check("all gains -> 100", approx(out[-1], 100.0))
falling = [float(i) for i in range(20, 1, -1)]
out = analysis.rsi(falling, 14)
check("all losses -> 0", approx(out[-1], 0.0))
# Constant closes: no gains, no losses -> avg_loss 0 -> convention 100
out = analysis.rsi([5.0] * 20, 14)
check("defined for flat series", out[-1] is not None)

print("MACD")
closes = [100.0 + i * 0.5 for i in range(60)]
line, signal, hist = analysis.macd(closes)
ema12 = analysis.ema(closes, 12)
ema26 = analysis.ema(closes, 26)
check("macd = ema12 - ema26", approx(line[-1], ema12[-1] - ema26[-1]))
check("histogram = macd - signal", approx(hist[-1], line[-1] - signal[-1]))
check("signal aligned", signal[-1] is not None and signal[24] is None)

print("Bollinger")
mid, up, low = analysis.bollinger([10.0] * 25)
check("flat series: bands collapse", approx(up[-1], 10.0) and approx(low[-1], 10.0) and approx(mid[-1], 10.0))
closes = [10.0, 12.0] * 15
mid, up, low = analysis.bollinger(closes)
check("upper above lower", up[-1] > mid[-1] > low[-1])

print("ATR")
n = 20
highs = [102.0] * n
lows = [98.0] * n
closes = [100.0] * n
out = analysis.atr(highs, lows, closes, 14)
check("constant range -> ATR = range", approx(out[-1], 4.0))
check("undefined before period+1", all(v is None for v in out[:14]))

print("last_cross")
a = [1.0, 1.0, 3.0, 3.0]
b = [2.0, 2.0, 2.0, 2.0]
check("cross up detected", analysis.last_cross(a, b) == ("up", 1))
a_down = [3.0, 3.0, 1.0, 1.0]
check("cross down detected", analysis.last_cross(a_down, b) == ("down", 1))
check("no cross", analysis.last_cross(b, b) is None)

print("pivot_levels")
# Price oscillating between ~90 (support) and ~110 (resistance)
highs, lows = [], []
for i in range(40):
    phase = i % 10
    mid_price = 100 + (10 if phase == 5 else -10 if phase == 0 else 0)
    highs.append(mid_price + 1.0)
    lows.append(mid_price - 1.0)
levels = analysis.pivot_levels(highs, lows)
prices = [lv["price"] for lv in levels]
check("finds levels", len(levels) >= 2)
check("support near 89", any(abs(p - 89) < 2 for p in prices), f"(got {prices})")
check("resistance near 111", any(abs(p - 111) < 2 for p in prices), f"(got {prices})")

print("analyze: shape and trimming")
closes = [100.0 * (1.002 ** i) for i in range(160)]  # steady uptrend
candles = make_candles(closes)
result = analysis.analyze(candles, display_count=96)
check("display candles trimmed", len(result["candles"]) == 96)
check("all strategies present", set(result["strategies"]) == {"trend", "rsi", "macd", "volatility", "levels_volume"})
trend = result["strategies"]["trend"]
check("trend series trimmed", len(trend["series"]["sma20"]) == 96)
check("series aligned with candles", trend["series"]["sma20"][0][0] == result["candles"][0][0])
check("warm-up makes SMA-50 defined on first bar", trend["series"]["sma50"][0][1] is not None)
check("explanations included", all("explanation" in s for s in result["strategies"].values()))
check("generated_at present", result["generated_at"].endswith("Z"))

print("analyze: signal classification")
check("uptrend -> trend bullish", trend["signal"] == "bullish", f"(got {trend['signal']}: {trend['reason']})")
rsi_strat = result["strategies"]["rsi"]
check("steady uptrend -> RSI overbought bearish", rsi_strat["signal"] == "bearish" and rsi_strat["reason"]["code"] == "overbought",
      f"(got {rsi_strat['signal']}: {rsi_strat['reason']})")
macd_strat = result["strategies"]["macd"]
check("uptrend -> MACD not bearish", macd_strat["signal"] in ("bullish", "neutral"), f"(got {macd_strat['signal']})")
vol = result["strategies"]["volatility"]
check("volatility has suggested stop below price", float(vol["values"]["suggested_stop"]) < closes[-1])

downtrend = make_candles([100.0 * (0.998 ** i) for i in range(160)])
result_down = analysis.analyze(downtrend, display_count=96)
check("downtrend -> trend bearish", result_down["strategies"]["trend"]["signal"] == "bearish",
      f"(got {result_down['strategies']['trend']['signal']})")
check("downtrend -> RSI oversold bullish", result_down["strategies"]["rsi"]["reason"]["code"] == "oversold")

print("analyze: insufficient data")
short = make_candles([100.0, 101.0, 102.0, 101.5, 103.0])
result_short = analysis.analyze(short, display_count=5)
for name, strat in result_short["strategies"].items():
    check(f"{name} degrades to none", strat["signal"] == "none" and strat["reason"]["code"] == "insufficient_data",
          f"(got {strat['signal']}: {strat['reason']})")

print("analyze: golden cross detection")
# Downtrend then sharp recovery so SMA-20 crosses above SMA-50 near the end
closes = [200.0 - i for i in range(100)] + [100.0 + 3.0 * i for i in range(30)]
result_cross = analysis.analyze(make_candles(closes), display_count=100)
trend_cross = result_cross["strategies"]["trend"]
check("golden cross flagged", trend_cross["reason"]["code"] in ("golden_cross", "uptrend"),
      f"(got {trend_cross['reason']})")

print("analyze: values serialized as strings")
for strat in result["strategies"].values():
    ok = all(v is None or isinstance(v, (str, int)) for v in strat["values"].values())
    check("values are strings", ok, f"(got {strat['values']})")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
