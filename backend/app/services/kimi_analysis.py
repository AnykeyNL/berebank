"""KimiK3 analysis: a single direction outlook blended from TA strategies.

Reuses the five strategy signals from ``analysis.analyze`` (single source of
truth for base indicator math), adds three KimiK3 price strategies — ADX
trend strength, dual-horizon momentum and a slow stochastic — and blends all
votes into one composite outlook: a direction (bullish/bearish/neutral), a
-100..+100 score, a confidence level and per-strategy contributions so users
can see exactly why.

On top of the price strategies, asset-class specific context signals join
the vote when supplementary data is available (see
``docs/external_source.md``):

- Crypto: Fear & Greed level and 7-day sentiment momentum, BTC dominance /
  stablecoin liquidity (BTC-aware: rising dominance is a bid for Bitcoin
  itself but a drain on altcoins), Coinglass funding level, 4h funding-rate
  momentum, price-confirmed open interest on two windows (4h/24h plus the
  intraday 1h edge), cross-exchange long/short taker ratio and 24h
  liquidation split.
- Stocks: VIX level and 5-day change, treasury yield curve, 20-day relative
  strength vs the sector SPDR ETF, an earnings-proximity brake and the
  90-day insider flow balance.
- Funds: VIX and yield curve with asset-aware routing — safe-haven bases
  (GLD, BND, TLT) read fear as a bid, and IBIT is routed the crypto macro
  context instead.
- Commodities: safe-haven VIX/yield logic for precious metals; the yield
  curve is omitted for energy futures where it is not predictive.

Weighting is regime-aware: when the ADX shows a strong trend (>= 25) the
trend-following strategies (trend, MACD, momentum) count double; in a
ranging market (ADX < 20) the mean-reversion strategies (RSI, Bollinger,
stochastic) count double. Context strategies always weigh 1.0. ``buy_score``
and ``sell_score`` (0..100) report the shares of active regime-weighted
weight voting bullish resp. bearish, so contested markets show high values
on both sides.
"""
from __future__ import annotations

from . import analysis

# Asset-class groupings for context-signal routing.
PRECIOUS_METALS = {"XAU", "XAG", "XPT", "XPD"}
ENERGY_COMMODITIES = {"WTI", "XBR", "URALS"}
# Funds that trade like their underlying: gold and Treasuries catch the
# risk-off bid; IBIT tracks Bitcoin, so it votes with crypto macro signals.
SAFE_HAVEN_BASES = PRECIOUS_METALS | {"GLD", "BND", "TLT"}
CRYPTO_LINKED_BASES = {"IBIT"}

EXPLANATIONS = {
    "trend_strength": (
        "The Average Directional Index (ADX, 14 bars) measures how strong "
        "the current trend is, regardless of direction: below 20 the market "
        "is ranging (no trend), above 25 the trend is strong. The +DI and "
        "-DI lines show which side drives it: +DI above -DI means buyers "
        "push harder."
    ),
    "momentum": (
        "Rate of change compares the current price with the price 10 and 20 "
        "bars ago. When both horizons are up, momentum is broadly positive; "
        "when they disagree, the move lacks follow-through."
    ),
    "stochastic": (
        "The slow stochastic oscillator (14, 3, 3) locates the price inside "
        "its recent high-low range: %K below 20 means oversold (bounce "
        "candidate), above 80 overbought (pullback risk), and a %K/%D cross "
        "in between signals momentum turning."
    ),
    "fear_greed_regime": (
        "The Crypto Fear & Greed Index blends volatility, momentum, social "
        "media and surveys into a 0-100 score. Extreme fear (25 or below) "
        "often marks capitulation; extreme greed (75+) can signal overheated "
        "conditions. In the middle zone, a fast 7-day improvement or "
        "deterioration acts as a short-term momentum signal."
    ),
    "crypto_liquidity": (
        "BTC dominance tracks Bitcoin's share of total crypto market cap: "
        "rising dominance concentrates capital in Bitcoin (good for BTC, a "
        "liquidity drain for altcoins), falling dominance fuels altseason. "
        "Stablecoin supply measures dry powder on the sidelines — growth "
        "supports risk appetite."
    ),
    "funding_regime": (
        "Aggregated perpetual funding rates across major exchanges. "
        "Extremely positive funding means crowded longs (contrarian "
        "bearish); deeply negative funding often marks short squeezes."
    ),
    "funding_momentum": (
        "The 24-hour trend of the funding rate itself (4h history). "
        "Funding rising while price rises means late longs are piling in "
        "leverage — fragile, contrarian bearish. Funding falling while "
        "price falls means shorts keep paying into the drop — capitulation "
        "fuel, contrarian bullish."
    ),
    "oi_momentum": (
        "Change in aggregate futures open interest (4h preferred, 24h "
        "fallback) read together with the price move over the same window. "
        "Rising OI while price rises means new longs drive the move "
        "(bullish); rising OI while price falls means new shorts drive it "
        "(bearish); falling OI means the move runs on closing positions "
        "and is losing fuel."
    ),
    "oi_fast": (
        "The 1-hour change in aggregate open interest — the fastest "
        "positioning signal available. A sharp OI expansion within the "
        "hour, confirmed by the price move over the same hour, shows new "
        "money entering on that side before slower indicators react."
    ),
    "long_short": (
        "Cross-exchange taker long/short volume ratio over the past 24h "
        "(Coinglass). A strong tilt to one side means the crowd is leaning "
        "that way — read contrarian at extremes, since crowded positioning "
        "is fragile."
    ),
    "liquidations": (
        "24h forced liquidations split into longs vs shorts across major "
        "exchanges (Coinglass). A heavy long flush clears leverage below "
        "the price (contrarian bounce setup); a heavy short squeeze spends "
        "the upside fuel (pullback risk)."
    ),
    "vix_regime": (
        "The CBOE Volatility Index (VIX) measures expected US equity "
        "volatility. Elevated VIX (25+) often coincides with risk-off "
        "conditions; subdued VIX (15 or below) suggests calmer markets. A "
        "fast VIX spike or cool-down over the past week is a short-term "
        "risk signal even when the level is mid-range. For gold, "
        "Treasuries and other safe havens, elevated fear tends to attract "
        "buying instead."
    ),
    "yield_curve": (
        "The spread between US 10-year and 2-year Treasury yields reflects "
        "growth expectations. An inverted curve (2Y above 10Y) is a "
        "classic recession warning — bad for stocks, but it drives "
        "safe-haven flows into gold and Treasuries. A steep positive "
        "spread supports risk appetite."
    ),
    "relative_strength": (
        "20-day return of the stock minus its sector SPDR ETF. Stocks "
        "leading their sector tend to keep outperforming over short "
        "horizons; laggards tend to stay weak."
    ),
    "event_risk": (
        "Earnings proximity. Within five days of a scheduled report the "
        "price can gap on results, so directional signals are less "
        "dependable — this signal votes neutral to pull the score toward "
        "the middle as a caution."
    ),
    "insider_flow": (
        "Balance of insider buying vs selling over the past 90 days "
        "(Twelve Data). Insiders buy their own stock mainly when they "
        "expect it to rise; heavy net selling is a warning sign."
    ),
}

# Backwards-compatible alias (was module-level before EXPLANATIONS existed).
TREND_STRENGTH_EXPLANATION = EXPLANATIONS["trend_strength"]

# Strategies computed from candles only; everything else needs live context.
PRICE_STRATEGIES = (
    "trend",
    "rsi",
    "macd",
    "volatility",
    "levels_volume",
    "trend_strength",
    "momentum",
    "stochastic",
)
CONTEXT_STRATEGIES = (
    "fear_greed_regime",
    "crypto_liquidity",
    "funding_regime",
    "funding_momentum",
    "oi_momentum",
    "oi_fast",
    "long_short",
    "liquidations",
    "vix_regime",
    "yield_curve",
    "relative_strength",
    "event_risk",
    "insider_flow",
)

# Fixed vote order so contributions render consistently.
STRATEGY_ORDER = list(PRICE_STRATEGIES) + list(CONTEXT_STRATEGIES)

# Regime-aware weighting sets (context strategies always weigh 1.0).
_TREND_FOLLOWING = {"trend", "macd", "momentum"}
_MEAN_REVERSION = {"rsi", "volatility", "stochastic"}

# Score thresholds on the -100..+100 scale.
_BULLISH_AT = 20
_BEARISH_AT = -20

# ADX regime thresholds.
_ADX_TRENDING = 25.0
_ADX_RANGING = 20.0

# Stochastic bands.
_STOCH_OVERSOLD = 20.0
_STOCH_OVERBOUGHT = 80.0

# Fear & Greed bands and 7-day sentiment momentum threshold.
_FG_GREED = 75
_FG_FEAR = 25
_FG_MOMENTUM = 10.0

# Crypto liquidity thresholds (percent change).
_DOMINANCE_MOVE = 0.5
_STABLECOIN_MOVE = 2.0
_LIQUIDITY_VOTE = 0.25

# Funding: level bands (contrarian) and 24h trend threshold (percentage
# points) with a 24h price-confirmation band.
_FUNDING_CROWDED_LONG = 0.05
_FUNDING_CROWDED_SHORT = -0.02
_FUNDING_TREND_MOVE = 0.02
_FUNDING_PRICE_CONFIRM = 0.5

# Open interest: 4h/24h windows with price confirmation, plus the 1h edge.
_OI_4H_MOVE = 2.0
_OI_24H_MOVE = 5.0
_OI_PRICE_CONFIRM = 0.2
_OI_1H_MOVE = 1.0
_OI_1H_PRICE_CONFIRM = 0.2

# Taker long/short volume ratio extremes (contrarian).
_LS_CROWDED_LONGS = 1.2
_LS_CROWDED_SHORTS = 1 / _LS_CROWDED_LONGS

# Liquidation split: one-sided share thresholds and a calm floor vs OI.
_LIQ_ONE_SIDED = 0.7
_LIQ_CALM_VS_OI = 0.0005

# VIX bands and 5-day change thresholds (percent).
_VIX_ELEVATED = 25.0
_VIX_CALM = 15.0
_VIX_SPIKE_PCT = 20.0
_VIX_COOL_PCT = -15.0

# Yield curve bands; US2Y-only fallback levels.
_YIELD_STEEP = 0.5
_US2Y_HIGH = 4.5
_US2Y_LOW = 3.0

# Stock relative strength vs sector ETF over 20 days (percent points).
_REL_STRENGTH = 2.0

# Earnings gap-risk window (days).
_EARNINGS_NEAR_DAYS = 5


# ---- KimiK3 indicator math (aligned lists; None while undefined) ----

def roc(closes: list[float], period: int) -> list[float | None]:
    """Rate of change in percent versus ``period`` bars ago."""
    out: list[float | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        prev = closes[i - period]
        if prev != 0:
            out[i] = (closes[i] / prev - 1) * 100
    return out


def trailing_change_pct(
    timestamps: list[int],
    closes: list[float],
    hours: float,
) -> float | None:
    """Close-to-close percent change over the trailing ``hours``.

    Uses the newest bar at or before ``hours`` ago so the window matches
    external stats (e.g. Coinglass 1h/4h/24h open-interest changes)
    regardless of the candle interval of the requested range.
    """
    if len(closes) < 2:
        return None
    target = timestamps[-1] - int(hours * 3_600_000)
    ref: float | None = None
    for i in range(len(timestamps) - 2, -1, -1):
        if timestamps[i] <= target:
            ref = closes[i]
            break
    if ref is None or ref == 0:
        return None
    return (closes[-1] / ref - 1) * 100


def _sma_optional(values: list[float | None], period: int) -> list[float | None]:
    """SMA over a series that may contain leading/embedded None values."""
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window) / period  # type: ignore[arg-type]
    return out


def stochastic(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
    smooth: int = 3,
) -> tuple[list[float | None], list[float | None]]:
    """Slow stochastic: (%K, %D), both smoothed with ``smooth``-bar SMAs.

    Bars whose high-low window is flat yield None instead of a division by
    zero, and the smoothing skips those gaps.
    """
    n = len(closes)
    raw: list[float | None] = [None] * n
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        if hh > ll:
            raw[i] = 100.0 * (closes[i] - ll) / (hh - ll)
    k = _sma_optional(raw, smooth)
    d = _sma_optional(k, smooth)
    return k, d


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Average Directional Index with +DI/-DI, Wilder-smoothed.

    Returns (adx, plus_di, minus_di) aligned with ``closes``; values are
    None until defined (ADX needs 2 * period bars).
    """
    n = len(closes)
    out_adx: list[float | None] = [None] * n
    out_pdi: list[float | None] = [None] * n
    out_mdi: list[float | None] = [None] * n
    if n < 2 * period:
        return out_adx, out_pdi, out_mdi

    trs: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dms.append(up if up > down and up > 0 else 0.0)
        minus_dms.append(down if down > up and down > 0 else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def _di(sm_tr: float, sm_dm: float) -> float:
        return 0.0 if sm_tr == 0 else 100.0 * sm_dm / sm_tr

    def _dx(pdi: float, mdi: float) -> float:
        total = pdi + mdi
        return 0.0 if total == 0 else 100.0 * abs(pdi - mdi) / total

    # Wilder smoothing of TR/+DM/-DM; sm_* at bar i covers trs[:i] (bars 1..i).
    sm_tr = sum(trs[:period])
    sm_plus = sum(plus_dms[:period])
    sm_minus = sum(minus_dms[:period])
    dxs: list[float | None] = [None] * n
    pdi, mdi = _di(sm_tr, sm_plus), _di(sm_tr, sm_minus)
    out_pdi[period], out_mdi[period] = pdi, mdi
    dxs[period] = _dx(pdi, mdi)
    for i in range(period + 1, n):
        sm_tr = sm_tr - sm_tr / period + trs[i - 1]
        sm_plus = sm_plus - sm_plus / period + plus_dms[i - 1]
        sm_minus = sm_minus - sm_minus / period + minus_dms[i - 1]
        pdi, mdi = _di(sm_tr, sm_plus), _di(sm_tr, sm_minus)
        out_pdi[i], out_mdi[i] = pdi, mdi
        dxs[i] = _dx(pdi, mdi)

    # First ADX is the SMA of the first `period` DX values (bars period..2p-1).
    window = [d for d in dxs[period : 2 * period] if d is not None]
    if len(window) < period:
        return out_adx, out_pdi, out_mdi
    prev = sum(window) / period
    out_adx[2 * period - 1] = prev
    for i in range(2 * period, n):
        prev = (prev * (period - 1) + dxs[i]) / period  # type: ignore[operator]
        out_adx[i] = prev
    return out_adx, out_pdi, out_mdi


def regime_for(adx_value: float | None) -> str:
    """Market regime used for vote weighting: trending | ranging | neutral."""
    if adx_value is None:
        return "neutral"
    if adx_value >= _ADX_TRENDING:
        return "trending"
    if adx_value < _ADX_RANGING:
        return "ranging"
    return "neutral"


def _insufficient(strategy: str) -> dict:
    return {
        "signal": "none",
        "reason": {"code": "insufficient_data", "params": {}},
        "explanation": EXPLANATIONS[strategy],
        "values": {},
        "series": {},
    }


# ---- KimiK3 price strategies ----

def _trend_strength(timestamps, highs, lows, closes, start) -> dict:
    """ADX-based trend strength/direction signal (the regime referee)."""
    if len(closes) < 30:
        return _insufficient("trend_strength")
    adx_values, plus_di, minus_di = adx(highs, lows, closes)
    current, pdi, mdi = adx_values[-1], plus_di[-1], minus_di[-1]
    if current is None or pdi is None or mdi is None:
        return _insufficient("trend_strength")

    regime = regime_for(current)
    if regime == "ranging":
        signal = "neutral"
        reason = {"code": "adx_ranging", "params": {"adx": analysis._s(current)}}
    else:
        up = pdi > mdi
        signal = "bullish" if up else "bearish"
        strength = "strong" if regime == "trending" else "weak"
        reason = {
            "code": f"adx_{strength}_{'up' if up else 'down'}",
            "params": {"adx": analysis._s(current)},
        }

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["trend_strength"],
        "values": {
            "adx": analysis._s(current),
            "plus_di": analysis._s(pdi),
            "minus_di": analysis._s(mdi),
            "regime": regime,
        },
        "series": {
            "adx": analysis._series(timestamps, adx_values, start),
            "plus_di": analysis._series(timestamps, plus_di, start),
            "minus_di": analysis._series(timestamps, minus_di, start),
        },
    }


def _momentum(timestamps, closes, start) -> dict:
    if len(closes) < 21:
        return _insufficient("momentum")
    roc10 = roc(closes, 10)
    roc20 = roc(closes, 20)
    r10, r20 = roc10[-1], roc20[-1]
    if r10 is None or r20 is None:
        return _insufficient("momentum")

    if r10 > 0 and r20 > 0:
        signal = "bullish"
        code = "momentum_up"
    elif r10 < 0 and r20 < 0:
        signal = "bearish"
        code = "momentum_down"
    else:
        signal = "neutral"
        code = "momentum_mixed"

    return {
        "signal": signal,
        "reason": {"code": code, "params": {"roc10": analysis._s(r10), "roc20": analysis._s(r20)}},
        "explanation": EXPLANATIONS["momentum"],
        "values": {"roc10": analysis._s(r10), "roc20": analysis._s(r20)},
        "series": {
            "roc10": analysis._series(timestamps, roc10, start),
            "roc20": analysis._series(timestamps, roc20, start),
        },
    }


def _stochastic_strategy(timestamps, highs, lows, closes, start) -> dict:
    if len(closes) < 20:
        return _insufficient("stochastic")
    k, d = stochastic(highs, lows, closes)
    ck, cd = k[-1], d[-1]
    if ck is None or cd is None:
        return _insufficient("stochastic")

    cross = analysis.last_cross(k, d, lookback=3)
    if ck < _STOCH_OVERSOLD:
        signal = "bullish"
        reason = {"code": "stoch_oversold", "params": {"k": analysis._s(ck)}}
    elif ck > _STOCH_OVERBOUGHT:
        signal = "bearish"
        reason = {"code": "stoch_overbought", "params": {"k": analysis._s(ck)}}
    elif cross is not None:
        direction, bars_ago = cross
        signal = "bullish" if direction == "up" else "bearish"
        code = "stoch_bull_cross" if direction == "up" else "stoch_bear_cross"
        reason = {"code": code, "params": {"bars_ago": bars_ago}}
    else:
        signal = "neutral"
        reason = {"code": "stoch_neutral", "params": {"k": analysis._s(ck), "d": analysis._s(cd)}}

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["stochastic"],
        "values": {"k": analysis._s(ck), "d": analysis._s(cd)},
        "series": {
            "k": analysis._series(timestamps, k, start),
            "d": analysis._series(timestamps, d, start),
        },
    }


# ---- Context strategies: crypto ----

def _fear_greed_regime(context: dict) -> dict:
    fg_raw = context.get("fear_greed_index")
    if fg_raw is None:
        return _insufficient("fear_greed_regime")
    fg = int(fg_raw)
    change = context.get("fear_greed_change")
    change_f = float(change) if change is not None else None
    if fg >= _FG_GREED:
        signal = "bearish"
        reason = {"code": "fear_greed_extreme_greed", "params": {"index": str(fg)}}
    elif fg <= _FG_FEAR:
        signal = "bullish"
        reason = {"code": "fear_greed_extreme_fear", "params": {"index": str(fg)}}
    elif change_f is not None and change_f >= _FG_MOMENTUM:
        signal = "bullish"
        reason = {"code": "fear_greed_improving", "params": {"index": str(fg), "change": analysis._s(change_f)}}
    elif change_f is not None and change_f <= -_FG_MOMENTUM:
        signal = "bearish"
        reason = {"code": "fear_greed_deteriorating", "params": {"index": str(fg), "change": analysis._s(change_f)}}
    else:
        signal = "neutral"
        reason = {"code": "fear_greed_neutral", "params": {"index": str(fg)}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["fear_greed_regime"],
        "values": {
            "fear_greed_index": str(fg),
            "classification": context.get("fear_greed_classification"),
            "change_7d": analysis._s(change_f),
        },
        "series": {},
    }


def _crypto_liquidity(context: dict) -> dict:
    """Dominance + stablecoin liquidity. BTC-aware: rising dominance is a
    bid for Bitcoin itself but drains altcoins; falling dominance fuels
    altseason but says little about Bitcoin's own price."""
    is_btc = str(context.get("base") or "").upper() == "BTC"
    votes: list[float] = []
    dom_change = context.get("btc_dominance_change_pct")
    stable_change = context.get("stablecoin_supply_change_pct")
    if dom_change is not None:
        dom_f = float(dom_change)
        if dom_f > _DOMINANCE_MOVE:
            votes.append(1.0 if is_btc else -1.0)
        elif dom_f < -_DOMINANCE_MOVE:
            votes.append(0.0 if is_btc else 1.0)
    if stable_change is not None:
        stable_f = float(stable_change)
        if stable_f > _STABLECOIN_MOVE:
            votes.append(1.0)
        elif stable_f < -_STABLECOIN_MOVE:
            votes.append(-1.0)
    if not votes:
        return _insufficient("crypto_liquidity")
    avg = sum(votes) / len(votes)
    if avg > _LIQUIDITY_VOTE:
        signal = "bullish"
        code = "crypto_liquidity_supportive"
    elif avg < -_LIQUIDITY_VOTE:
        signal = "bearish"
        code = "crypto_liquidity_tight"
    else:
        signal = "neutral"
        code = "crypto_liquidity_mixed"
    return {
        "signal": signal,
        "reason": {
            "code": code,
            "params": {
                "dominance_change_pct": analysis._s(float(dom_change)) if dom_change is not None else None,
                "stablecoin_change_pct": analysis._s(float(stable_change)) if stable_change is not None else None,
            },
        },
        "explanation": EXPLANATIONS["crypto_liquidity"],
        "values": {
            "btc_dominance": analysis._s(float(context["btc_dominance"]))
            if context.get("btc_dominance") is not None
            else None,
            "stablecoin_supply_change_pct": analysis._s(float(stable_change))
            if stable_change is not None
            else None,
        },
        "series": {},
    }


def _funding_regime(context: dict) -> dict:
    if context.get("funding_rate_avg") is None:
        return _insufficient("funding_regime")
    funding = float(context["funding_rate_avg"])
    if funding >= _FUNDING_CROWDED_LONG:
        signal = "bearish"
        reason = {"code": "funding_crowded_longs", "params": {"funding": analysis._s(funding)}}
    elif funding <= _FUNDING_CROWDED_SHORT:
        signal = "bullish"
        reason = {"code": "funding_crowded_shorts", "params": {"funding": analysis._s(funding)}}
    else:
        signal = "neutral"
        reason = {"code": "funding_neutral", "params": {"funding": analysis._s(funding)}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["funding_regime"],
        "values": {"funding_rate_avg": analysis._s(funding)},
        "series": {},
    }


def _funding_momentum(context: dict, timestamps: list[int], closes: list[float]) -> dict:
    """24h funding-rate trend (4h history) read with the 24h price move.

    Contrarian: funding rising into a rising price means late leveraged
    longs; funding falling into a falling price means shorts keep paying
    into the drop — both are exhaustion setups.
    """
    trend = context.get("funding_rate_change_24h")
    if trend is None:
        return _insufficient("funding_momentum")
    trend_f = float(trend)
    price_chg = trailing_change_pct(timestamps, closes, 24)
    params = {"change": analysis._s(trend_f), "price_change": analysis._s(price_chg)}
    if abs(trend_f) < _FUNDING_TREND_MOVE:
        signal, code = "neutral", "funding_trend_flat"
    elif price_chg is None or abs(price_chg) <= _FUNDING_PRICE_CONFIRM:
        signal, code = "neutral", "funding_trend_unclear"
    elif trend_f > 0 and price_chg > _FUNDING_PRICE_CONFIRM:
        signal, code = "bearish", "funding_crowding_longs"
    elif trend_f < 0 and price_chg < -_FUNDING_PRICE_CONFIRM:
        signal, code = "bullish", "funding_capitulating"
    else:
        signal, code = "neutral", "funding_diverging"
    return {
        "signal": signal,
        "reason": {"code": code, "params": params},
        "explanation": EXPLANATIONS["funding_momentum"],
        "values": {
            "funding_rate_change_24h": analysis._s(trend_f),
            "price_change_24h_pct": analysis._s(price_chg),
            "reference_exchange": context.get("funding_rate_reference_exchange"),
        },
        "series": {},
    }


def _oi_momentum(context: dict, timestamps: list[int], closes: list[float]) -> dict:
    """Open-interest change (4h preferred, 24h fallback) read together with
    the price move over the same window: OI expanding with the move confirms
    it; OI contracting means the move runs on closing positions."""
    oi_4h = context.get("open_interest_change_percent_4h")
    oi_24h = context.get("open_interest_change_percent_24h")
    if oi_4h is None and oi_24h is None:
        return _insufficient("oi_momentum")

    # Prefer the 4h window when it moves; fall back to 24h.
    if oi_4h is not None and abs(float(oi_4h)) >= _OI_4H_MOVE:
        change, threshold, hours = float(oi_4h), _OI_4H_MOVE, 4
    elif oi_24h is not None:
        change, threshold, hours = float(oi_24h), _OI_24H_MOVE, 24
    else:
        change, threshold, hours = float(oi_4h), _OI_4H_MOVE, 4

    price_chg = trailing_change_pct(timestamps, closes, hours)
    params = {
        "change": analysis._s(change),
        "hours": hours,
        "price_change": analysis._s(price_chg),
    }
    if change >= threshold:
        if price_chg is None:
            signal, code = "bullish", "oi_rising"
        elif price_chg > _OI_PRICE_CONFIRM:
            signal, code = "bullish", "oi_confirming_up"
        elif price_chg < -_OI_PRICE_CONFIRM:
            signal, code = "bearish", "oi_confirming_down"
        else:
            signal, code = "neutral", "oi_stable"
    elif change <= -threshold:
        if price_chg is not None and abs(price_chg) > _OI_PRICE_CONFIRM:
            signal, code = "neutral", "oi_unwinding"
        elif price_chg is None:
            signal, code = "bearish", "oi_falling"
        else:
            signal, code = "neutral", "oi_stable"
    else:
        signal, code = "neutral", "oi_stable"

    return {
        "signal": signal,
        "reason": {"code": code, "params": params},
        "explanation": EXPLANATIONS["oi_momentum"],
        "values": {
            "open_interest_change_percent_24h": analysis._s(float(oi_24h)) if oi_24h is not None else None,
            "open_interest_change_percent_4h": analysis._s(float(oi_4h)) if oi_4h is not None else None,
            "window_hours": str(hours),
            "price_change_pct": analysis._s(price_chg),
        },
        "series": {},
    }


def _oi_fast(context: dict, timestamps: list[int], closes: list[float]) -> dict:
    """The intraday edge: 1h open-interest change, price-confirmed over the
    same hour. A sharp 1h OI expansion shows new positions entering on that
    side before slower signals react; a contraction is just unwinding."""
    oi_1h = context.get("open_interest_change_percent_1h")
    if oi_1h is None:
        return _insufficient("oi_fast")
    change = float(oi_1h)
    price_chg = trailing_change_pct(timestamps, closes, 1)
    params = {"change": analysis._s(change), "price_change": analysis._s(price_chg)}
    if change >= _OI_1H_MOVE:
        if price_chg is not None and price_chg > _OI_1H_PRICE_CONFIRM:
            signal, code = "bullish", "oi_fast_longs"
        elif price_chg is not None and price_chg < -_OI_1H_PRICE_CONFIRM:
            signal, code = "bearish", "oi_fast_shorts"
        else:
            signal, code = "neutral", "oi_fast_unconfirmed"
    elif change <= -_OI_1H_MOVE:
        signal, code = "neutral", "oi_fast_unwinding"
    else:
        signal, code = "neutral", "oi_fast_calm"
    return {
        "signal": signal,
        "reason": {"code": code, "params": params},
        "explanation": EXPLANATIONS["oi_fast"],
        "values": {
            "open_interest_change_percent_1h": analysis._s(change),
            "price_change_1h_pct": analysis._s(price_chg),
        },
        "series": {},
    }


def _long_short(context: dict) -> dict:
    if context.get("long_short_ratio") is None:
        return _insufficient("long_short")
    ratio = float(context["long_short_ratio"])
    if ratio >= _LS_CROWDED_LONGS:
        signal = "bearish"
        reason = {"code": "ls_crowded_longs", "params": {"ratio": analysis._s(ratio)}}
    elif ratio <= _LS_CROWDED_SHORTS:
        signal = "bullish"
        reason = {"code": "ls_crowded_shorts", "params": {"ratio": analysis._s(ratio)}}
    else:
        signal = "neutral"
        reason = {"code": "ls_balanced", "params": {"ratio": analysis._s(ratio)}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["long_short"],
        "values": {"long_short_ratio": analysis._s(ratio)},
        "series": {},
    }


def _liquidations(context: dict) -> dict:
    long_liq = context.get("long_liquidation_usd_24h")
    short_liq = context.get("short_liquidation_usd_24h")
    if long_liq is None and short_liq is None:
        return _insufficient("liquidations")
    long_f = float(long_liq or 0.0)
    short_f = float(short_liq or 0.0)
    total = long_f + short_f

    oi_usd = context.get("open_interest_usd")
    calm = total <= 0
    if not calm and oi_usd:
        try:
            calm = total / float(oi_usd) < _LIQ_CALM_VS_OI
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    params = {
        "long_usd": analysis._s(long_f),
        "short_usd": analysis._s(short_f),
    }
    if calm:
        signal = "neutral"
        reason = {"code": "liq_calm", "params": params}
    else:
        long_share = long_f / total
        if long_share >= _LIQ_ONE_SIDED:
            # Leveraged longs already flushed out: contrarian bounce setup.
            signal = "bullish"
            reason = {"code": "liq_long_flush", "params": params}
        elif long_share <= 1 - _LIQ_ONE_SIDED:
            # Short squeeze spent the upside fuel: pullback risk.
            signal = "bearish"
            reason = {"code": "liq_short_squeeze", "params": params}
        else:
            signal = "neutral"
            reason = {"code": "liq_balanced", "params": params}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["liquidations"],
        "values": {
            "long_liquidation_usd_24h": analysis._s(long_f),
            "short_liquidation_usd_24h": analysis._s(short_f),
        },
        "series": {},
    }


# ---- Context strategies: stocks / funds / commodities ----

def _is_haven(context: dict | None) -> bool:
    return bool(context) and str(context.get("base") or "").upper() in SAFE_HAVEN_BASES


def _vix_regime(context: dict) -> dict:
    if context.get("vix_level") is None:
        return _insufficient("vix_regime")
    vix = float(context["vix_level"])
    change = context.get("vix_change_pct")
    change_f = float(change) if change is not None else None

    if _is_haven(context):
        # Fear is a safe-haven bid for gold and Treasuries, not a sell signal.
        if vix >= _VIX_ELEVATED or (change_f is not None and change_f >= _VIX_SPIKE_PCT):
            signal = "bullish"
            reason = {"code": "vix_haven_bid", "params": {"vix": analysis._s(vix)}}
        else:
            signal = "neutral"
            reason = {"code": "vix_neutral", "params": {"vix": analysis._s(vix)}}
    elif vix >= _VIX_ELEVATED:
        signal = "bearish"
        reason = {"code": "vix_elevated", "params": {"vix": analysis._s(vix)}}
    elif vix <= _VIX_CALM:
        signal = "bullish"
        reason = {"code": "vix_calm", "params": {"vix": analysis._s(vix)}}
    elif change_f is not None and change_f >= _VIX_SPIKE_PCT:
        signal = "bearish"
        reason = {"code": "vix_spiking", "params": {"vix": analysis._s(vix), "change": analysis._s(change_f)}}
    elif change_f is not None and change_f <= _VIX_COOL_PCT:
        signal = "bullish"
        reason = {"code": "vix_cooling", "params": {"vix": analysis._s(vix), "change": analysis._s(change_f)}}
    else:
        signal = "neutral"
        reason = {"code": "vix_neutral", "params": {"vix": analysis._s(vix)}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["vix_regime"],
        "values": {"vix": analysis._s(vix), "change_5d_pct": analysis._s(change_f)},
        "series": {},
    }


def _yield_curve(context: dict) -> dict:
    spread = context.get("yield_spread")
    us2y = context.get("us2y_yield")
    us10y = context.get("us10y_yield")
    haven = _is_haven(context)
    if spread is not None and us2y is not None and us10y is not None:
        spread_f = float(spread)
        if haven:
            # Recession signals drive safe-haven flows into gold/Treasuries.
            if spread_f < 0:
                signal = "bullish"
                reason = {"code": "yield_haven_inverted", "params": {"spread": analysis._s(spread_f)}}
            else:
                signal = "neutral"
                reason = {"code": "yield_flat", "params": {"spread": analysis._s(spread_f)}}
        elif spread_f < 0:
            signal = "bearish"
            reason = {"code": "yield_inverted", "params": {"spread": analysis._s(spread_f)}}
        elif spread_f > _YIELD_STEEP:
            signal = "bullish"
            reason = {"code": "yield_steep", "params": {"spread": analysis._s(spread_f)}}
        else:
            signal = "neutral"
            reason = {"code": "yield_flat", "params": {"spread": analysis._s(spread_f)}}
        return {
            "signal": signal,
            "reason": reason,
            "explanation": EXPLANATIONS["yield_curve"],
            "values": {
                "spread": analysis._s(spread_f),
                "us2y": analysis._s(float(us2y)),
                "us10y": analysis._s(float(us10y)),
            },
            "series": {},
        }
    if us2y is not None:
        # High short rates pressure both stocks and duration assets.
        us2y_f = float(us2y)
        if us2y_f >= _US2Y_HIGH:
            signal = "bearish"
            reason = {"code": "yield_2y_elevated", "params": {"us2y": analysis._s(us2y_f)}}
        elif us2y_f <= _US2Y_LOW:
            signal = "bullish"
            reason = {"code": "yield_2y_low", "params": {"us2y": analysis._s(us2y_f)}}
        else:
            signal = "neutral"
            reason = {"code": "yield_2y_neutral", "params": {"us2y": analysis._s(us2y_f)}}
        return {
            "signal": signal,
            "reason": reason,
            "explanation": EXPLANATIONS["yield_curve"],
            "values": {"us2y": analysis._s(us2y_f)},
            "series": {},
        }
    return _insufficient("yield_curve")


def _relative_strength(context: dict) -> dict:
    if context.get("sector_relative_return") is None:
        return _insufficient("relative_strength")
    rel = float(context["sector_relative_return"])
    etf = context.get("sector_etf") or ""
    params = {"rel": analysis._s(rel), "etf": etf}
    if rel >= _REL_STRENGTH:
        signal = "bullish"
        reason = {"code": "rel_strength_leading", "params": params}
    elif rel <= -_REL_STRENGTH:
        signal = "bearish"
        reason = {"code": "rel_strength_lagging", "params": params}
    else:
        signal = "neutral"
        reason = {"code": "rel_strength_inline", "params": params}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["relative_strength"],
        "values": {"sector_relative_return": analysis._s(rel), "sector_etf": etf},
        "series": {},
    }


def _event_risk(context: dict) -> dict:
    if context.get("days_to_earnings") is None:
        return _insufficient("event_risk")
    days = int(context["days_to_earnings"])
    if 0 <= days <= _EARNINGS_NEAR_DAYS:
        # Neutral vote on purpose: it dilutes the score toward the middle
        # while the gap risk of an imminent earnings report is live.
        signal = "neutral"
        reason = {"code": "earnings_near", "params": {"days": days}}
    else:
        signal = "none"
        reason = {"code": "earnings_far", "params": {"days": days}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["event_risk"],
        "values": {"days_to_earnings": str(days)},
        "series": {},
    }


def _insider_flow(context: dict) -> dict:
    insider = context.get("insider_signal")
    buys = int(context.get("insider_buys", 0) or 0)
    sells = int(context.get("insider_sells", 0) or 0)
    params = {"buys": buys, "sells": sells}
    if insider == "bullish":
        signal = "bullish"
        reason = {"code": "insider_net_buying", "params": params}
    elif insider == "bearish":
        signal = "bearish"
        reason = {"code": "insider_net_selling", "params": params}
    else:
        # Balanced or absent insider activity carries no directional edge.
        signal = "none"
        reason = {"code": "insider_balanced", "params": params}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["insider_flow"],
        "values": {"insider_signal": insider, "insider_buys": str(buys), "insider_sells": str(sells)},
        "series": {},
    }


# ---- composite outlook ----

def compute_outlook(strategies: dict, context: dict | None = None) -> dict:
    """Blend strategy signals into one direction outlook.

    Each strategy votes +1 (bullish), -1 (bearish) or 0 (neutral); "none"
    strategies are excluded. The score is the weighted vote share scaled to
    -100..+100; confidence reflects what fraction of active strategies
    agrees with the resulting direction.

    Weighting is regime-aware: in a strong trend (ADX >= 25) the
    trend-following strategies count double; in a ranging market (ADX < 20)
    the mean-reversion strategies count double (suppressed near earnings or
    extreme funding, where mean reversion is unreliable). Context
    strategies always weigh 1.0.

    ``buy_score`` and ``sell_score`` (0..100) are the shares of active
    regime-weighted weight voting bullish resp. bearish. Unlike the net
    score they surface contested markets: high buy AND high sell means the
    evidence is split, not absent.
    """
    ts_values = strategies.get("trend_strength", {}).get("values", {})
    adx_raw = ts_values.get("adx")
    regime = regime_for(float(adx_raw) if adx_raw else None)
    earnings_near = bool(context and context.get("earnings_near"))
    funding_extreme = False
    if context:
        funding = context.get("funding_rate_avg")
        if funding is not None and abs(float(funding)) >= _FUNDING_CROWDED_LONG:
            funding_extreme = True

    contributions = []
    for key in STRATEGY_ORDER:
        strategy = strategies.get(key)
        if strategy is None:
            continue
        signal = strategy.get("signal", "none")
        weight = 1.0
        if signal != "none":
            if regime == "trending" and key in _TREND_FOLLOWING:
                weight = 2.0
            elif (
                regime == "ranging"
                and key in _MEAN_REVERSION
                and not earnings_near
                and not funding_extreme
            ):
                weight = 2.0
        contributions.append({"strategy": key, "signal": signal, "weight": weight})

    active = [c for c in contributions if c["signal"] != "none"]
    if not active:
        return {
            "direction": "none",
            "score": 0,
            "buy_score": 0,
            "sell_score": 0,
            "confidence": "low",
            "regime": regime,
            "reason": {"code": "outlook_no_data", "params": {}},
            "contributions": contributions,
        }

    vote = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}
    total_weight = sum(c["weight"] for c in active)
    weighted = sum(vote[c["signal"]] * c["weight"] for c in active)
    score = round(100 * weighted / total_weight)
    bullish_weight = sum(c["weight"] for c in active if c["signal"] == "bullish")
    bearish_weight = sum(c["weight"] for c in active if c["signal"] == "bearish")
    buy_score = round(100 * bullish_weight / total_weight)
    sell_score = round(100 * bearish_weight / total_weight)

    if score >= _BULLISH_AT:
        direction = "bullish"
    elif score <= _BEARISH_AT:
        direction = "bearish"
    else:
        direction = "neutral"

    agreeing = sum(1 for c in active if c["signal"] == direction)
    agreement = agreeing / len(active)
    confidence = "high" if agreement >= 0.8 else "medium" if agreement >= 0.6 else "low"

    counts = {s: sum(1 for c in active if c["signal"] == s) for s in ("bullish", "bearish", "neutral")}
    reason_params = {**counts, "total": len(active), "regime": regime}
    if earnings_near:
        reason_params["earnings_near"] = True
    if funding_extreme:
        reason_params["funding_extreme"] = True
    return {
        "direction": direction,
        "score": score,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "confidence": confidence,
        "regime": regime,
        "reason": {
            "code": f"outlook_{direction}",
            "params": reason_params,
        },
        "contributions": contributions,
    }


# ---- entry point ----

def _context_asset_class(context: dict | None) -> str | None:
    if not context:
        return None
    asset_class = context.get("asset_class")
    if asset_class:
        return str(asset_class)
    return "crypto" if context.get("context_type") == "crypto" else None


def analyze_kimi(
    candles: list[list],
    display_count: int,
    context: dict | None = None,
) -> dict:
    """KimiK3 outlook over ``candles`` (oldest first, API candle shape).

    Same contract as ``analysis.analyze``: ``display_count`` trailing bars
    form the display window, earlier bars are warm-up only. Context signals
    join the vote per asset class (crypto derivative slots never appear for
    equities and vice versa; IBIT votes with crypto macro signals);
    without context — e.g. the walk-forward track record — only the eight
    price strategies vote.
    """
    base = analysis.analyze(candles, display_count)
    timestamps = [int(c[0]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    start = max(0, len(candles) - display_count)

    strategies = {
        **base["strategies"],
        "trend_strength": _trend_strength(timestamps, highs, lows, closes, start),
        "momentum": _momentum(timestamps, closes, start),
        "stochastic": _stochastic_strategy(timestamps, highs, lows, closes, start),
    }

    asset_class = _context_asset_class(context)
    base_symbol = str(context.get("base") or "").upper() if context else ""
    if context is not None:
        if asset_class == "crypto" or base_symbol in CRYPTO_LINKED_BASES:
            strategies["fear_greed_regime"] = _fear_greed_regime(context)
            strategies["crypto_liquidity"] = _crypto_liquidity(context)
            strategies["funding_regime"] = _funding_regime(context)
        if asset_class == "crypto":
            strategies["funding_momentum"] = _funding_momentum(context, timestamps, closes)
            strategies["oi_momentum"] = _oi_momentum(context, timestamps, closes)
            strategies["oi_fast"] = _oi_fast(context, timestamps, closes)
            strategies["long_short"] = _long_short(context)
            strategies["liquidations"] = _liquidations(context)
        if asset_class in ("stock", "fund", "commodity"):
            strategies["vix_regime"] = _vix_regime(context)
            # The treasury curve says little about oil; skip it for energy.
            if not (asset_class == "commodity" and base_symbol in ENERGY_COMMODITIES):
                strategies["yield_curve"] = _yield_curve(context)
        if asset_class == "stock":
            strategies["relative_strength"] = _relative_strength(context)
            strategies["event_risk"] = _event_risk(context)
            strategies["insider_flow"] = _insider_flow(context)

    return {
        "generated_at": base["generated_at"],
        "candles": base["candles"],
        "outlook": compute_outlook(strategies, context),
        "strategies": strategies,
    }
