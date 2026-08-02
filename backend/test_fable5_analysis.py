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

print("trailing_change_pct")
hourly = [
    [1_700_000_000_000 + i * 3_600_000, "0", "0", "0", str(100.0 + i), "0"]
    for i in range(30)
]
ts_h = [int(c[0]) for c in hourly]
cl_h = [float(c[4]) for c in hourly]
chg = fable5_analysis.trailing_change_pct(ts_h, cl_h, 24)
check("24h change from hourly bars", chg is not None and abs(chg - (129.0 / 105.0 - 1) * 100) < 1e-9, f"got {chg}")
check("window longer than history -> None", fable5_analysis.trailing_change_pct(ts_h[:2], cl_h[:2], 24) is None)

print("regime_for")
check("trending at 25", fable5_analysis.regime_for(25.0) == "trending")
check("ranging below 20", fable5_analysis.regime_for(19.9) == "ranging")
check("neutral between", fable5_analysis.regime_for(22.0) == "neutral")
check("neutral without ADX", fable5_analysis.regime_for(None) == "neutral")

print("compute_outlook")
all_up = strategies({k: "bullish" for k in fable5_analysis.STRATEGY_ORDER})
out = fable5_analysis.compute_outlook(all_up)
check("all bullish -> bullish 100 high", out["direction"] == "bullish" and out["score"] == 100 and out["confidence"] == "high")
check("all bullish -> buy 100 / sell 0", out["buy_score"] == 100 and out["sell_score"] == 0)

all_down = strategies({k: "bearish" for k in fable5_analysis.STRATEGY_ORDER})
out = fable5_analysis.compute_outlook(all_down)
check("all bearish -> bearish -100", out["direction"] == "bearish" and out["score"] == -100)
check("all bearish -> buy 0 / sell 100", out["buy_score"] == 0 and out["sell_score"] == 100)

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
# buy = 3.5/4.5 = 78, sell = 1/4.5 = 22; both sides visible despite bullish verdict.
check("buy/sell shares expose the split", out["buy_score"] == 78 and out["sell_score"] == 22, f"got {out['buy_score']}/{out['sell_score']}")

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
check("no data -> buy/sell 0", out["buy_score"] == 0 and out["sell_score"] == 0)

partial = strategies({
    "trend": "bullish", "macd": "none", "momentum": "none", "rsi": "none",
    "stochastic": "none", "volatility": "none", "levels_volume": "none",
    "trend_strength": "none",
})
out = fable5_analysis.compute_outlook(partial)
check("single active strategy decides", out["direction"] == "bullish" and out["score"] == 100)
check("confidence high when all active weight agrees", out["confidence"] == "high")

PRICE_STRATEGIES = {
    "trend", "macd", "momentum", "trend_strength",
    "rsi", "stochastic", "volatility", "levels_volume",
}

print("analyze_fable5")
up_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(140)])
result = fable5_analysis.analyze_fable5(up_candles, 80)
check("price strategies only without context", set(result["strategies"]) == PRICE_STRATEGIES)
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

ctx_spike = {"vix_level": 20.0, "vix_change_pct": 30.0}
ctx_cool = {"vix_level": 20.0, "vix_change_pct": -20.0}
check("VIX spike bearish mid-range", fable5_analysis._vix_regime(ctx_spike)["signal"] == "bearish")
check("VIX spike reason", fable5_analysis._vix_regime(ctx_spike)["reason"]["code"] == "vix_spiking")
check("VIX cool-down bullish mid-range", fable5_analysis._vix_regime(ctx_cool)["signal"] == "bullish")

print("precious metals macro")
ctx_gold_fear = {"vix_level": 30.0, "asset_class": "commodity", "base": "XAU"}
ctx_gold_calm = {"vix_level": 13.0, "asset_class": "commodity", "base": "XAU"}
check("elevated VIX bullish for gold (haven bid)", fable5_analysis._vix_regime(ctx_gold_fear)["signal"] == "bullish")
check("calm VIX neutral for gold", fable5_analysis._vix_regime(ctx_gold_calm)["signal"] == "neutral")
ctx_gold_inv = {**ctx_inv, "asset_class": "commodity", "base": "XAG"}
ctx_gold_steep = {**ctx_low, "asset_class": "commodity", "base": "XAU"}
check("inverted curve bullish for precious metals", fable5_analysis._yield_curve(ctx_gold_inv)["signal"] == "bullish")
check("steep curve neutral for precious metals", fable5_analysis._yield_curve(ctx_gold_steep)["signal"] == "neutral")

print("crypto macro strategies")
ctx_fg_greed = {"context_type": "crypto", "fear_greed_index": 80}
ctx_fg_fear = {"context_type": "crypto", "fear_greed_index": 15}
check("extreme greed bearish", fable5_analysis._vix_regime(ctx_fg_greed)["signal"] == "bearish")
check("extreme fear bullish", fable5_analysis._vix_regime(ctx_fg_fear)["signal"] == "bullish")
ctx_fg_up = {"context_type": "crypto", "fear_greed_index": 55, "fear_greed_change": 15.0}
ctx_fg_down = {"context_type": "crypto", "fear_greed_index": 55, "fear_greed_change": -15.0}
check("mid-range sentiment improving bullish", fable5_analysis._vix_regime(ctx_fg_up)["signal"] == "bullish")
check("mid-range sentiment deteriorating bearish", fable5_analysis._vix_regime(ctx_fg_down)["signal"] == "bearish")
ctx_liq = {
    "context_type": "crypto",
    "btc_dominance": 54.0,
    "btc_dominance_change_pct": -1.0,
    "stablecoin_supply_change_pct": 4.0,
}
check("supportive crypto liquidity", fable5_analysis._yield_curve(ctx_liq)["signal"] == "bullish")

print("derivatives strategies")
ts_d = [int(c[0]) for c in up_candles]
cl_up = [float(100.0 * math.exp(0.01 * i)) for i in range(140)]
cl_down = [float(300.0 * math.exp(-0.01 * i)) for i in range(140)]
ctx_oi_up = {"context_type": "crypto", "open_interest_change_percent_24h": 8.0}
oi = fable5_analysis._oi_momentum(ctx_oi_up, ts_d, cl_up)
check("OI up + price up -> bullish", oi["signal"] == "bullish" and oi["reason"]["code"] == "oi_confirming_up", f"got {oi['reason']}")
oi = fable5_analysis._oi_momentum(ctx_oi_up, ts_d, cl_down)
check("OI up + price down -> bearish (new shorts)", oi["signal"] == "bearish" and oi["reason"]["code"] == "oi_confirming_down")
ctx_oi_down = {"context_type": "crypto", "open_interest_change_percent_24h": -8.0}
oi = fable5_analysis._oi_momentum(ctx_oi_down, ts_d, cl_up)
check("OI down while price moves -> neutral unwinding", oi["signal"] == "neutral" and oi["reason"]["code"] == "oi_unwinding")
ctx_oi_4h = {
    "context_type": "crypto",
    "open_interest_change_percent_4h": 3.0,
    "open_interest_change_percent_24h": 0.5,
}
oi = fable5_analysis._oi_momentum(ctx_oi_4h, ts_d, cl_up)
check("4h OI move preferred over flat 24h", oi["values"]["window_hours"] == "4" and oi["signal"] == "bullish", f"got {oi}")

check("crowded longs bearish", fable5_analysis._long_short({"long_short_ratio": 1.3})["signal"] == "bearish")
check("crowded shorts bullish", fable5_analysis._long_short({"long_short_ratio": 0.75})["signal"] == "bullish")
check("balanced ratio neutral", fable5_analysis._long_short({"long_short_ratio": 1.0})["signal"] == "neutral")
check("no ratio -> none", fable5_analysis._long_short({})["signal"] == "none")

liq_flush = {"long_liquidation_usd_24h": 9e6, "short_liquidation_usd_24h": 1e6, "open_interest_usd": 1e9}
liq_squeeze = {"long_liquidation_usd_24h": 1e6, "short_liquidation_usd_24h": 9e6, "open_interest_usd": 1e9}
liq_calm = {"long_liquidation_usd_24h": 1e4, "short_liquidation_usd_24h": 1e4, "open_interest_usd": 1e9}
check("long flush -> contrarian bullish", fable5_analysis._liquidations(liq_flush)["signal"] == "bullish")
check("short squeeze -> contrarian bearish", fable5_analysis._liquidations(liq_squeeze)["signal"] == "bearish")
liq_calm_result = fable5_analysis._liquidations(liq_calm)
check("tiny liquidations vs OI -> calm neutral", liq_calm_result["signal"] == "neutral" and liq_calm_result["reason"]["code"] == "liq_calm")

print("stock strategies")
check("sector leader bullish", fable5_analysis._relative_strength({"sector_relative_return": 3.5, "sector_etf": "XLK"})["signal"] == "bullish")
check("sector laggard bearish", fable5_analysis._relative_strength({"sector_relative_return": -3.0, "sector_etf": "XLK"})["signal"] == "bearish")
check("in-line neutral", fable5_analysis._relative_strength({"sector_relative_return": 0.5, "sector_etf": "XLK"})["signal"] == "neutral")
event_near = fable5_analysis._event_risk({"days_to_earnings": 2})
check("earnings near -> neutral brake", event_near["signal"] == "neutral" and event_near["reason"]["code"] == "earnings_near")
event_far = fable5_analysis._event_risk({"days_to_earnings": 30})
check("earnings far -> excluded from vote", event_far["signal"] == "none" and event_far["reason"]["code"] == "earnings_far")

print("asset-class gating")
crypto_result = fable5_analysis.analyze_fable5(up_candles, 80, ctx_fg_fear)
check(
    "crypto context adds crypto slots",
    set(crypto_result["strategies"]) == PRICE_STRATEGIES | {"vix_regime", "yield_curve", "funding_regime", "oi_momentum", "long_short", "liquidations"},
    f"got {sorted(crypto_result['strategies'])}",
)
stock_ctx = {**ctx_low, "asset_class": "stock", "base": "AAPL", "sector_relative_return": 2.5, "sector_etf": "XLK", "days_to_earnings": 20}
stock_result = fable5_analysis.analyze_fable5(up_candles, 80, stock_ctx)
check(
    "stock context adds stock slots, no crypto slots",
    set(stock_result["strategies"]) == PRICE_STRATEGIES | {"vix_regime", "yield_curve", "relative_strength", "event_risk"},
    f"got {sorted(stock_result['strategies'])}",
)
oil_ctx = {**ctx_low, "asset_class": "commodity", "base": "WTI"}
oil_result = fable5_analysis.analyze_fable5(up_candles, 80, oil_ctx)
check(
    "energy commodity skips the yield curve",
    set(oil_result["strategies"]) == PRICE_STRATEGIES | {"vix_regime"},
    f"got {sorted(oil_result['strategies'])}",
)
fund_result = fable5_analysis.analyze_fable5(up_candles, 80, {**ctx_low, "asset_class": "fund", "base": "SPY"})
check(
    "fund context keeps macro slots only",
    set(fund_result["strategies"]) == PRICE_STRATEGIES | {"vix_regime", "yield_curve"},
    f"got {sorted(fund_result['strategies'])}",
)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
