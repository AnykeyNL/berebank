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


def hourly(n=48):
    """Hourly timestamps plus flat closes, for windowed price-change tests."""
    return [1_700_000_000_000 + i * 3_600_000 for i in range(n)], [100.0] * n


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
check("all bullish -> buy 100 sell 0", out["buy_score"] == 100 and out["sell_score"] == 0)

all_down = strategies({k: "bearish" for k in kimi_analysis.STRATEGY_ORDER})
out = kimi_analysis.compute_outlook(all_down)
check("all bearish -> bearish -100", out["direction"] == "bearish" and out["score"] == -100)
check("all bearish -> buy 0 sell 100", out["buy_score"] == 0 and out["sell_score"] == 100)

mixed = strategies({
    "trend": "bullish", "rsi": "bullish", "macd": "bullish",
    "volatility": "bearish", "levels_volume": "bearish", "trend_strength": "bearish",
})
out = kimi_analysis.compute_outlook(mixed)
check("split vote -> neutral score 0", out["direction"] == "neutral" and out["score"] == 0)
check("split vote -> low confidence", out["confidence"] == "low")
check("split vote -> contested buy/sell 50/50", out["buy_score"] == 50 and out["sell_score"] == 50)

none_all = strategies({k: "none" for k in kimi_analysis.STRATEGY_ORDER})
out = kimi_analysis.compute_outlook(none_all)
check("no data -> direction none", out["direction"] == "none" and out["reason"]["code"] == "outlook_no_data")
check("no data -> buy/sell 0", out["buy_score"] == 0 and out["sell_score"] == 0)

trending = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=30.0)
out = kimi_analysis.compute_outlook(trending)
weights = {c["strategy"]: c["weight"] for c in out["contributions"]}
check("trending regime doubles trend+macd+momentum",
      weights["trend"] == 2.0 and weights["macd"] == 2.0 and weights["momentum"] == 2.0)
check("trending regime leaves mean-reversion at 1", weights["rsi"] == 1.0 and weights["stochastic"] == 1.0)
check("trending regime never doubles context votes", weights["fear_greed_regime"] == 1.0 and weights["vix_regime"] == 1.0)
check("regime reported", out["regime"] == "trending")

ranging = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=10.0)
out = kimi_analysis.compute_outlook(ranging)
weights = {c["strategy"]: c["weight"] for c in out["contributions"]}
check("ranging regime doubles rsi+volatility+stochastic",
      weights["rsi"] == 2.0 and weights["volatility"] == 2.0 and weights["stochastic"] == 2.0)
check("ranging regime leaves trend-following at 1", weights["trend"] == 1.0 and weights["momentum"] == 1.0)

# Score exactly at the +20 threshold: votes +1,+1,0,0,-1 over 5 active -> 20.
edge = strategies({
    "trend": "bullish", "rsi": "bullish", "macd": "neutral",
    "volatility": "neutral", "levels_volume": "bearish", "trend_strength": "none",
})
out = kimi_analysis.compute_outlook(edge)
check("score 20 is bullish", out["score"] == 20 and out["direction"] == "bullish", f"got {out['score']}")
check("edge case buy 40 sell 20", out["buy_score"] == 40 and out["sell_score"] == 20)

# "none" strategies are excluded from the vote entirely.
partial = strategies({
    "trend": "bullish", "rsi": "none", "macd": "none",
    "volatility": "none", "levels_volume": "none", "trend_strength": "none",
})
out = kimi_analysis.compute_outlook(partial)
check("single active strategy decides", out["direction"] == "bullish" and out["score"] == 100)
check("confidence high when 1/1 agrees", out["confidence"] == "high")

print("regime suppression brakes")
ranging = strategies({k: "bullish" for k in kimi_analysis.STRATEGY_ORDER}, adx_value=10.0)
out_plain = kimi_analysis.compute_outlook(ranging)
out_earnings = kimi_analysis.compute_outlook(ranging, {"earnings_near": True})
w_plain = {c["strategy"]: c["weight"] for c in out_plain["contributions"]}
w_earnings = {c["strategy"]: c["weight"] for c in out_earnings["contributions"]}
check("earnings_near suppresses mean-reversion doubling",
      w_plain["rsi"] == 2.0 and w_earnings["rsi"] == 1.0 and w_earnings["stochastic"] == 1.0)

out_funding = kimi_analysis.compute_outlook(ranging, {"funding_rate_avg": 0.06})
w_funding = {c["strategy"]: c["weight"] for c in out_funding["contributions"]}
check("extreme funding suppresses mean-reversion doubling", w_funding["rsi"] == 1.0 and w_funding["stochastic"] == 1.0)

print("context votes join the blend")
flat_prices = strategies({k: "neutral" for k in kimi_analysis.PRICE_STRATEGIES})
flat_prices["funding_regime"] = strat("bearish")
flat_prices["long_short"] = strat("bearish")
out = kimi_analysis.compute_outlook(flat_prices)
check("two bearish context votes tilt neutral prices bearish",
      out["direction"] == "bearish" and out["score"] == -20, f"got {out['score']}")
check("context tilt shows in sell score", out["sell_score"] == 20 and out["buy_score"] == 0)

print("crypto context strategies")
check("extreme greed -> contrarian bearish",
      kimi_analysis._fear_greed_regime({"fear_greed_index": 82})["signal"] == "bearish")
check("extreme fear -> contrarian bullish",
      kimi_analysis._fear_greed_regime({"fear_greed_index": 18})["signal"] == "bullish")
check("fast improving sentiment -> bullish",
      kimi_analysis._fear_greed_regime({"fear_greed_index": 50, "fear_greed_change": "12"})["reason"]["code"] == "fear_greed_improving")
check("flat mid-zone -> neutral",
      kimi_analysis._fear_greed_regime({"fear_greed_index": 50})["signal"] == "neutral")
check("missing fear&greed -> none",
      kimi_analysis._fear_greed_regime({})["signal"] == "none")

liq = kimi_analysis._crypto_liquidity(
    {"base": "ETH", "btc_dominance_change_pct": "0.8", "stablecoin_supply_change_pct": "-3.0"})
check("alt: rising dominance + draining stables -> bearish",
      liq["signal"] == "bearish" and liq["reason"]["code"] == "crypto_liquidity_tight")
liq = kimi_analysis._crypto_liquidity({"base": "BTC", "btc_dominance_change_pct": "0.8"})
check("BTC: rising dominance is a bid for bitcoin -> bullish", liq["signal"] == "bullish")
liq = kimi_analysis._crypto_liquidity({"base": "ETH", "btc_dominance_change_pct": "-0.9"})
check("alt: falling dominance fuels altseason -> bullish", liq["signal"] == "bullish")
check("no liquidity data -> none", kimi_analysis._crypto_liquidity({"base": "ETH"})["signal"] == "none")

check("crowded longs funding -> contrarian bearish",
      kimi_analysis._funding_regime({"funding_rate_avg": "0.08"})["signal"] == "bearish")
check("deeply negative funding -> squeeze bullish",
      kimi_analysis._funding_regime({"funding_rate_avg": "-0.03"})["signal"] == "bullish")
check("moderate funding -> neutral",
      kimi_analysis._funding_regime({"funding_rate_avg": "0.01"})["signal"] == "neutral")

ts, base_closes = hourly(48)
up_24h = base_closes[:24] + [101.0] * 24
fm = kimi_analysis._funding_momentum({"funding_rate_change_24h": "0.03"}, ts, up_24h)
check("funding rising into rising price -> crowding bearish",
      fm["signal"] == "bearish" and fm["reason"]["code"] == "funding_crowding_longs")
down_24h = [101.0] * 24 + base_closes[:24]
fm = kimi_analysis._funding_momentum({"funding_rate_change_24h": "-0.03"}, ts, down_24h)
check("funding falling into falling price -> capitulation bullish",
      fm["signal"] == "bullish" and fm["reason"]["code"] == "funding_capitulating")
fm = kimi_analysis._funding_momentum({"funding_rate_change_24h": "0.005"}, ts, up_24h)
check("flat funding trend -> neutral", fm["signal"] == "neutral" and fm["reason"]["code"] == "funding_trend_flat")
check("no funding history -> none", kimi_analysis._funding_momentum({}, ts, up_24h)["signal"] == "none")

oi_up = base_closes[:44] + [100.2, 100.4, 100.6, 100.8]
oi = kimi_analysis._oi_momentum({"open_interest_change_percent_4h": "3.0"}, ts, oi_up)
check("4h OI up + price up -> new longs confirm (bullish)",
      oi["signal"] == "bullish" and oi["reason"]["code"] == "oi_confirming_up")
oi = kimi_analysis._oi_momentum(
    {"open_interest_change_percent_4h": "0.5", "open_interest_change_percent_24h": "6.0"}, ts, up_24h)
check("quiet 4h falls back to 24h window", oi["reason"]["params"]["hours"] == 24 and oi["signal"] == "bullish")
oi = kimi_analysis._oi_momentum({"open_interest_change_percent_24h": "-7.0"}, ts, up_24h)
check("OI shrinking into a move -> unwinding (neutral)", oi["signal"] == "neutral" and oi["reason"]["code"] == "oi_unwinding")

fast_up = base_closes[:-2] + [100.0, 100.5]
oi = kimi_analysis._oi_fast({"open_interest_change_percent_1h": "1.5"}, ts, fast_up)
check("1h OI spike + 1h price up -> fast longs (bullish)",
      oi["signal"] == "bullish" and oi["reason"]["code"] == "oi_fast_longs")
oi = kimi_analysis._oi_fast({"open_interest_change_percent_1h": "1.5"}, ts, base_closes)
check("1h OI spike without price move -> unconfirmed (neutral)",
      oi["signal"] == "neutral" and oi["reason"]["code"] == "oi_fast_unconfirmed")
oi = kimi_analysis._oi_fast({"open_interest_change_percent_1h": "-1.4"}, ts, fast_up)
check("1h OI drop -> unwinding (neutral)", oi["signal"] == "neutral" and oi["reason"]["code"] == "oi_fast_unwinding")
check("no 1h OI -> none", kimi_analysis._oi_fast({}, ts, fast_up)["signal"] == "none")

check("crowded taker longs -> contrarian bearish",
      kimi_analysis._long_short({"long_short_ratio": "1.35"})["signal"] == "bearish")
check("crowded taker shorts -> squeeze bullish",
      kimi_analysis._long_short({"long_short_ratio": "0.75"})["signal"] == "bullish")
check("balanced taker flow -> neutral",
      kimi_analysis._long_short({"long_short_ratio": "1.05"})["signal"] == "neutral")

liqd = kimi_analysis._liquidations({
    "long_liquidation_usd_24h": "8000000", "short_liquidation_usd_24h": "2000000",
    "open_interest_usd": "1000000000"})
check("one-sided long flush -> contrarian bounce (bullish)",
      liqd["signal"] == "bullish" and liqd["reason"]["code"] == "liq_long_flush")
liqd = kimi_analysis._liquidations({
    "long_liquidation_usd_24h": "1000000", "short_liquidation_usd_24h": "9000000",
    "open_interest_usd": "1000000000"})
check("one-sided short squeeze -> pullback risk (bearish)",
      liqd["signal"] == "bearish" and liqd["reason"]["code"] == "liq_short_squeeze")
liqd = kimi_analysis._liquidations({
    "long_liquidation_usd_24h": "1000", "short_liquidation_usd_24h": "1000",
    "open_interest_usd": "1000000000"})
check("tiny liquidations vs OI -> calm", liqd["signal"] == "neutral" and liqd["reason"]["code"] == "liq_calm")

print("stock/fund/commodity context strategies")
check("VIX 30 -> risk-off bearish for stocks",
      kimi_analysis._vix_regime({"vix_level": "30"})["signal"] == "bearish")
check("VIX 12 -> calm bullish",
      kimi_analysis._vix_regime({"vix_level": "12"})["reason"]["code"] == "vix_calm")
check("VIX spike from normal level -> bearish",
      kimi_analysis._vix_regime({"vix_level": "20", "vix_change_pct": "25"})["reason"]["code"] == "vix_spiking")
haven = kimi_analysis._vix_regime({"vix_level": "30", "base": "GLD"})
check("haven base reads VIX 30 as a safe-haven bid (bullish)",
      haven["signal"] == "bullish" and haven["reason"]["code"] == "vix_haven_bid")

yc = kimi_analysis._yield_curve({"yield_spread": "-0.3", "us2y_yield": "4.6", "us10y_yield": "4.3"})
check("inverted curve -> bearish for stocks", yc["signal"] == "bearish" and yc["reason"]["code"] == "yield_inverted")
yc = kimi_analysis._yield_curve({"yield_spread": "-0.3", "us2y_yield": "4.6", "us10y_yield": "4.3", "base": "TLT"})
check("inverted curve -> haven-demand bullish for TLT", yc["signal"] == "bullish" and yc["reason"]["code"] == "yield_haven_inverted")
yc = kimi_analysis._yield_curve({"yield_spread": "0.8", "us2y_yield": "3.4", "us10y_yield": "4.2"})
check("steep curve -> growth-friendly bullish", yc["signal"] == "bullish")
yc = kimi_analysis._yield_curve({"us2y_yield": "5.0"})
check("US2Y-only fallback: 5% -> tight bearish", yc["signal"] == "bearish" and yc["reason"]["code"] == "yield_2y_elevated")
check("no yields at all -> none", kimi_analysis._yield_curve({})["signal"] == "none")

check("leading its sector by 3% -> bullish",
      kimi_analysis._relative_strength({"sector_relative_return": "3.1", "sector_etf": "XLK"})["signal"] == "bullish")
check("lagging its sector by 3% -> bearish",
      kimi_analysis._relative_strength({"sector_relative_return": "-3.1", "sector_etf": "XLK"})["signal"] == "bearish")

check("earnings in 3 days -> neutral brake vote",
      kimi_analysis._event_risk({"days_to_earnings": 3})["signal"] == "neutral")
far = kimi_analysis._event_risk({"days_to_earnings": 40})
check("earnings far away -> no vote", far["signal"] == "none" and far["reason"]["code"] == "earnings_far")

check("net insider buying -> bullish",
      kimi_analysis._insider_flow({"insider_signal": "bullish", "insider_buys": 5, "insider_sells": 1})["signal"] == "bullish")
check("no insider edge -> no vote",
      kimi_analysis._insider_flow({"insider_signal": "neutral", "insider_buys": 2, "insider_sells": 2})["signal"] == "none")

print("momentum + stochastic price strategies")
ts30 = list(range(30))
mom = kimi_analysis._momentum(ts30, [100.0 + i for i in range(30)], 0)
check("both ROC horizons up -> momentum_up bullish",
      mom["signal"] == "bullish" and mom["reason"]["code"] == "momentum_up")
mixed_closes = [100.0] * 10 + [110.0] * 11 + [105.0] * 9
mom = kimi_analysis._momentum(ts30, mixed_closes, 0)
check("ROC horizons disagree -> momentum_mixed neutral",
      mom["signal"] == "neutral" and mom["reason"]["code"] == "momentum_mixed")

falls = [100.0 - i for i in range(30)]
stoc = kimi_analysis._stochastic_strategy(ts30, [c + 0.5 for c in falls], [c - 0.5 for c in falls], falls, 0)
check("close pinned at range low -> oversold bullish",
      stoc["signal"] == "bullish" and stoc["reason"]["code"] == "stoch_oversold", f"got {stoc['reason']}")

print("analyze_kimi routing")
up_candles = make_candles([100.0 * math.exp(0.002 * i) for i in range(140)])
result = kimi_analysis.analyze_kimi(up_candles, 80)
check("eight price strategies without context",
      set(result["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES))
check("uptrend -> bullish outlook", result["outlook"]["direction"] == "bullish")
check("trend_strength signal", result["strategies"]["trend_strength"]["signal"] == "bullish")
check("reason code matches direction", result["outlook"]["reason"]["code"] == "outlook_bullish")
check("candles trimmed to display window", len(result["candles"]) == 80)
check("price-only buy/sell scores present", 0 <= result["outlook"]["buy_score"] <= 100 and 0 <= result["outlook"]["sell_score"] <= 100)

crypto = kimi_analysis.analyze_kimi(up_candles, 80, {"asset_class": "crypto", "base": "BTC"})
check("crypto context adds the 8 derivative/macro slots",
      set(crypto["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES) | {
          "fear_greed_regime", "crypto_liquidity", "funding_regime", "funding_momentum",
          "oi_momentum", "oi_fast", "long_short", "liquidations"})
check("empty context fields vote none",
      all(crypto["strategies"][k]["signal"] == "none" for k in kimi_analysis.CONTEXT_STRATEGIES if k in crypto["strategies"]))

stock = kimi_analysis.analyze_kimi(up_candles, 80, {"asset_class": "stock", "base": "AAPL"})
check("stock context adds equity slots, no crypto slots",
      set(stock["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES) | {
          "vix_regime", "yield_curve", "relative_strength", "event_risk", "insider_flow"})

ibit = kimi_analysis.analyze_kimi(up_candles, 80, {"asset_class": "fund", "base": "IBIT"})
check("IBIT votes with crypto macro + fund macro, no derivative slots",
      set(ibit["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES) | {
          "fear_greed_regime", "crypto_liquidity", "funding_regime", "vix_regime", "yield_curve"})

wti = kimi_analysis.analyze_kimi(up_candles, 80, {"asset_class": "commodity", "base": "WTI"})
check("energy commodity gets VIX but not the yield curve",
      set(wti["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES) | {"vix_regime"})

xau = kimi_analysis.analyze_kimi(up_candles, 80, {"asset_class": "commodity", "base": "XAU"})
check("precious metal gets VIX + yield curve",
      set(xau["strategies"]) == set(kimi_analysis.PRICE_STRATEGIES) | {"vix_regime", "yield_curve"})

full_crypto = kimi_analysis.analyze_kimi(up_candles, 80, {
    "asset_class": "crypto", "base": "BTC",
    "fear_greed_index": 88, "funding_rate_avg": "0.09", "long_short_ratio": "1.4",
    "long_liquidation_usd_24h": "1000000", "short_liquidation_usd_24h": "9000000",
    "open_interest_usd": "1000000000",
})
price_only_score = result["outlook"]["score"]
check("bearish derivative votes drag the bullish price score down",
      full_crypto["outlook"]["score"] < price_only_score,
      f"{full_crypto['outlook']['score']} vs {price_only_score}")
check("crowded-market context lifts the sell score",
      full_crypto["outlook"]["sell_score"] > result["outlook"]["sell_score"])

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
