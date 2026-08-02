"""Fable5 analysis: a fixed-weight direction gauge blended from many signals.

Reuses the five strategy signals from ``analysis.analyze`` (single source of
truth for base indicator math) and adds three Fable5-specific price strategies:
dual-horizon momentum (rate of change over 10 and 20 bars), a slow stochastic
oscillator (14, 3, 3) and ADX trend strength. On top of that, asset-class
specific context signals join the vote when supplementary data is available:

- All non-crypto: VIX level and 5-day VIX change, treasury yield curve
  (inverted for precious metals, omitted for energy futures where it is not
  predictive).
- Crypto: Fear & Greed level and 7-day sentiment momentum, BTC dominance /
  stablecoin liquidity, Coinglass funding, price-confirmed open-interest
  momentum, cross-exchange long/short taker ratio and 24h liquidation split.
- Stocks: 20-day relative strength vs the sector SPDR ETF and an earnings
  proximity brake that pulls the score toward neutral in gap-risk windows.

All votes blend into one composite outlook: a direction (bullish/bearish/
neutral), a -100..+100 score rendered as a five-zone gauge in the web app, a
confidence level and per-strategy contributions so users can see exactly why.

Unlike KimiK3's regime-aware weighting, Fable5 uses fixed importance weights
that never change with market conditions: the recipe users see is always the
same. The ADX market regime is reported as context only. Confidence is the
weighted share of active strategies agreeing with the verdict.
"""
from __future__ import annotations

from . import analysis

# Commodity bases whose macro signals need special treatment.
PRECIOUS_METALS = {"XAU", "XAG", "XPT", "XPD"}
ENERGY_COMMODITIES = {"WTI", "XBR", "URALS"}

EXPLANATIONS = {
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
    "trend_strength": (
        "The Average Directional Index (ADX, 14 bars) measures how strong "
        "the current trend is, regardless of direction: below 20 the market "
        "is ranging (no trend), above 25 the trend is strong. The +DI and "
        "-DI lines show which side drives it."
    ),
    "vix_regime": (
        "The CBOE Volatility Index (VIX) measures expected US equity "
        "volatility. Elevated VIX (25+) often coincides with risk-off "
        "conditions; subdued VIX (15 or below) suggests calmer markets. A "
        "fast VIX spike or cool-down over the past week is a short-term "
        "risk signal even when the level is mid-range. For gold and other "
        "precious metals, elevated fear tends to attract safe-haven buying."
    ),
    "fear_greed_regime": (
        "The Crypto Fear & Greed Index blends volatility, momentum, social "
        "media and surveys into a 0–100 score. Extreme fear (25 or below) "
        "often marks capitulation; extreme greed (75+) can signal overheated "
        "conditions. In the middle zone, a fast 7-day improvement or "
        "deterioration in sentiment acts as a short-term momentum signal."
    ),
    "yield_curve": (
        "The spread between US 10-year and 2-year Treasury yields reflects "
        "growth expectations. An inverted curve (2Y above 10Y) is a classic "
        "recession warning; a steep positive spread supports risk appetite."
    ),
    "crypto_liquidity": (
        "BTC dominance tracks Bitcoin's share of total crypto market cap; "
        "rising dominance often drains altcoin liquidity. Stablecoin supply "
        "measures dry powder on the sidelines — growth supports risk appetite."
    ),
    "funding_regime": (
        "Aggregated perpetual funding rates across major exchanges. "
        "Extremely positive funding means crowded longs (contrarian bearish); "
        "deeply negative funding often marks short squeezes."
    ),
    "oi_momentum": (
        "Change in aggregate futures open interest (4h preferred, 24h "
        "fallback) read together with the price move over the same window. "
        "Rising OI while price rises means new longs drive the move "
        "(bullish); rising OI while price falls means new shorts drive it "
        "(bearish); falling OI means the move is running on closing "
        "positions and is losing fuel."
    ),
    "long_short": (
        "Cross-exchange taker long/short volume ratio over the past 24h "
        "(Coinglass). A strong tilt to one side means the crowd is leaning "
        "that way — read contrarian at extremes, since crowded positioning "
        "is fragile."
    ),
    "liquidations": (
        "24h forced liquidations split into longs vs shorts across major "
        "exchanges (Coinglass). A heavy long flush clears leverage below the "
        "price (contrarian bounce setup); a heavy short squeeze spends the "
        "upside fuel (pullback risk)."
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
}

# Fixed vote order so contributions render consistently.
STRATEGY_ORDER = [
    "trend",
    "macd",
    "momentum",
    "trend_strength",
    "vix_regime",
    "yield_curve",
    "funding_regime",
    "oi_momentum",
    "long_short",
    "liquidations",
    "relative_strength",
    "event_risk",
    "rsi",
    "stochastic",
    "volatility",
    "levels_volume",
]

# Fixed importance weights; deliberately independent of market regime.
WEIGHTS = {
    "trend": 2.0,
    "macd": 1.5,
    "momentum": 1.5,
    "trend_strength": 1.5,
    "vix_regime": 1.0,
    "yield_curve": 1.0,
    "funding_regime": 1.0,
    "oi_momentum": 1.0,
    "long_short": 1.0,
    "liquidations": 1.0,
    "relative_strength": 1.0,
    "event_risk": 1.0,
    "rsi": 1.0,
    "stochastic": 1.0,
    "volatility": 1.0,
    "levels_volume": 1.0,
}

# Score thresholds on the -100..+100 scale.
_BULLISH_AT = 20
_BEARISH_AT = -20

# Weighted-agreement confidence thresholds.
_HIGH_AGREEMENT = 0.75
_MEDIUM_AGREEMENT = 0.55

# ADX thresholds (standard Wilder readings).
_ADX_STRONG = 25.0
_ADX_MILD = 20.0

# Stochastic bands.
_STOCH_OVERSOLD = 20.0
_STOCH_OVERBOUGHT = 80.0

# Fear & Greed bands and 7-day sentiment momentum threshold.
_FG_GREED = 75
_FG_FEAR = 25
_FG_MOMENTUM = 10.0

# VIX bands and 5-day change thresholds (percent).
_VIX_ELEVATED = 25.0
_VIX_CALM = 15.0
_VIX_SPIKE_PCT = 20.0
_VIX_COOL_PCT = -15.0

# Open interest: preferred 4h window, 24h fallback, with price confirmation.
_OI_4H_MOVE = 2.0
_OI_24H_MOVE = 5.0
_OI_PRICE_CONFIRM = 0.2

# Taker long/short volume ratio extremes (contrarian).
_LS_CROWDED_LONGS = 1.2
_LS_CROWDED_SHORTS = 1 / _LS_CROWDED_LONGS

# Liquidation split: one-sided share thresholds and a calm floor vs OI.
_LIQ_ONE_SIDED = 0.7
_LIQ_CALM_VS_OI = 0.0005

# Stock relative strength vs sector ETF over 20 days (percent points).
_REL_STRENGTH = 2.0

# Earnings gap-risk window (days).
_EARNINGS_NEAR_DAYS = 5


# ---- Fable5 indicator math (aligned lists; None while undefined) ----

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
    external stats (e.g. Coinglass 4h/24h open-interest changes) regardless
    of the candle interval of the requested range.
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
    """Wilder ADX with +DI/-DI, aligned with ``closes``.

    Values are None until defined (ADX needs 2 * period bars). Own
    implementation per the analyzer isolation contract.
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
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dms.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dms.append(down_move if down_move > up_move and down_move > 0 else 0.0)
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

    # Wilder smoothing; the smoothed sums at bar i cover bars 1..i.
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

    # First ADX is the SMA of the first `period` DX values.
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
    """Market regime shown as context: trending | ranging | neutral."""
    if adx_value is None:
        return "neutral"
    if adx_value >= _ADX_STRONG:
        return "trending"
    if adx_value < _ADX_MILD:
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


# ---- Fable5 strategies ----

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


def _trend_strength(timestamps, highs, lows, closes, start) -> dict:
    if len(closes) < 30:
        return _insufficient("trend_strength")
    adx_values, plus_di, minus_di = adx(highs, lows, closes)
    current, pdi, mdi = adx_values[-1], plus_di[-1], minus_di[-1]
    if current is None or pdi is None or mdi is None:
        return _insufficient("trend_strength")

    if current < _ADX_MILD:
        signal = "neutral"
        reason = {"code": "adx_ranging", "params": {"adx": analysis._s(current)}}
    else:
        up = pdi > mdi
        strength = "strong" if current >= _ADX_STRONG else "mild"
        signal = "bullish" if up else "bearish"
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
            "regime": regime_for(current),
        },
        "series": {
            "adx": analysis._series(timestamps, adx_values, start),
            "plus_di": analysis._series(timestamps, plus_di, start),
            "minus_di": analysis._series(timestamps, minus_di, start),
        },
    }


def _fear_greed_regime(context: dict) -> dict:
    fg = int(context["fear_greed_index"])
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


def _is_precious_metal(context: dict | None) -> bool:
    return bool(context) and str(context.get("base") or "").upper() in PRECIOUS_METALS


def _vix_regime(context: dict | None) -> dict:
    if context and context.get("fear_greed_index") is not None:
        return _fear_greed_regime(context)
    if not context or context.get("vix_level") is None:
        return _insufficient("vix_regime")
    vix = float(context["vix_level"])
    change = context.get("vix_change_pct")
    change_f = float(change) if change is not None else None

    if _is_precious_metal(context):
        # Fear is a safe-haven bid for gold and its peers, not a sell signal.
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


def _crypto_liquidity(context: dict) -> dict:
    votes: list[float] = []
    dom_change = context.get("btc_dominance_change_pct")
    stable_change = context.get("stablecoin_supply_change_pct")
    if dom_change is not None:
        dom_f = float(dom_change)
        if dom_f > 0.5:
            votes.append(-1.0)
        elif dom_f < -0.5:
            votes.append(1.0)
    if stable_change is not None:
        stable_f = float(stable_change)
        if stable_f > 2.0:
            votes.append(1.0)
        elif stable_f < -2.0:
            votes.append(-1.0)
    if not votes:
        return _insufficient("yield_curve")
    avg = sum(votes) / len(votes)
    if avg > 0.25:
        signal = "bullish"
        code = "crypto_liquidity_supportive"
    elif avg < -0.25:
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
            "btc_dominance": analysis._s(context.get("btc_dominance")),
            "stablecoin_supply_change_pct": analysis._s(stable_change),
        },
        "series": {},
    }


def _yield_curve(context: dict | None) -> dict:
    if context and context.get("context_type") == "crypto":
        return _crypto_liquidity(context)
    spread = context.get("yield_spread") if context else None
    us2y = context.get("us2y_yield") if context else None
    us10y = context.get("us10y_yield") if context else None
    precious = _is_precious_metal(context)
    if spread is not None and us2y is not None and us10y is not None:
        spread_f = float(spread)
        if precious:
            # Recession signals attract safe-haven flows into precious metals.
            if spread_f < 0:
                signal = "bullish"
                reason = {"code": "yield_pm_inverted", "params": {"spread": analysis._s(spread_f)}}
            elif spread_f > 0.5:
                signal = "neutral"
                reason = {"code": "yield_pm_steep", "params": {"spread": analysis._s(spread_f)}}
            else:
                signal = "neutral"
                reason = {"code": "yield_flat", "params": {"spread": analysis._s(spread_f)}}
        elif spread_f < 0:
            signal = "bearish"
            reason = {"code": "yield_inverted", "params": {"spread": analysis._s(spread_f)}}
        elif spread_f > 0.5:
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
        us2y_f = float(us2y)
        if us2y_f >= 4.5:
            signal = "bearish"
            reason = {"code": "yield_2y_elevated", "params": {"us2y": analysis._s(us2y_f)}}
        elif us2y_f <= 3.0:
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


def _funding_regime(context: dict | None) -> dict:
    if not context or context.get("funding_rate_avg") is None:
        return _insufficient("funding_regime")
    funding = float(context["funding_rate_avg"])
    if funding >= 0.05:
        signal = "bearish"
        reason = {"code": "funding_crowded_longs", "params": {"funding": analysis._s(funding)}}
    elif funding <= -0.02:
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


def _oi_momentum(context: dict | None, timestamps: list[int], closes: list[float]) -> dict:
    """Open-interest change read together with the price move over the same
    window: OI expanding with the price move confirms it (new positions on the
    winning side); OI contracting means the move runs on closing positions."""
    if not context:
        return _insufficient("oi_momentum")
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


def _long_short(context: dict | None) -> dict:
    if not context or context.get("long_short_ratio") is None:
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


def _liquidations(context: dict | None) -> dict:
    if not context:
        return _insufficient("liquidations")
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


def _relative_strength(context: dict | None) -> dict:
    if not context or context.get("sector_relative_return") is None:
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


def _event_risk(context: dict | None) -> dict:
    if not context or context.get("days_to_earnings") is None:
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


# ---- composite outlook ----

def compute_outlook(strategies: dict) -> dict:
    """Blend the available strategy signals into one direction outlook.

    Each strategy votes +1 (bullish), -1 (bearish) or 0 (neutral) with its
    fixed weight; "none" strategies are excluded. The score is the weighted
    vote share scaled to -100..+100; confidence is the weighted share of
    active strategies agreeing with the resulting direction.

    ``buy_score`` and ``sell_score`` (0..100) are the shares of active
    weight voting bullish resp. bearish. Unlike the net score they surface
    contested markets: high buy AND high sell means the evidence is split,
    not absent.
    """
    ts_values = strategies.get("trend_strength", {}).get("values", {})
    adx_raw = ts_values.get("adx")
    regime = regime_for(float(adx_raw) if adx_raw else None)

    contributions = [
        {
            "strategy": key,
            "signal": strategies[key].get("signal", "none"),
            "weight": WEIGHTS[key],
        }
        for key in STRATEGY_ORDER
        if key in strategies
    ]

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

    agreeing_weight = sum(c["weight"] for c in active if c["signal"] == direction)
    agreement = agreeing_weight / total_weight
    if agreement >= _HIGH_AGREEMENT:
        confidence = "high"
    elif agreement >= _MEDIUM_AGREEMENT:
        confidence = "medium"
    else:
        confidence = "low"

    counts = {s: sum(1 for c in active if c["signal"] == s) for s in ("bullish", "bearish", "neutral")}
    return {
        "direction": direction,
        "score": score,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "confidence": confidence,
        "regime": regime,
        "reason": {
            "code": f"outlook_{direction}",
            "params": {**counts, "total": len(active)},
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


def analyze_fable5(
    candles: list[list],
    display_count: int,
    context: dict | None = None,
) -> dict:
    """Fable5 outlook over ``candles`` (oldest first, API candle shape).

    Same contract as ``analysis.analyze``: ``display_count`` trailing bars
    form the display window, earlier bars are warm-up only. Context signals
    join the vote per asset class, so an equity user never sees crypto
    derivative slots and vice versa; without context (e.g. the walk-forward
    track record) only the eight price strategies vote.
    """
    base = analysis.analyze(candles, display_count)
    timestamps = [int(c[0]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    start = max(0, len(candles) - display_count)

    strategies = {
        **base["strategies"],
        "momentum": _momentum(timestamps, closes, start),
        "stochastic": _stochastic_strategy(timestamps, highs, lows, closes, start),
        "trend_strength": _trend_strength(timestamps, highs, lows, closes, start),
    }

    asset_class = _context_asset_class(context)
    base_symbol = str(context.get("base") or "").upper() if context else ""
    if context is not None:
        strategies["vix_regime"] = _vix_regime(context)
        # The treasury curve says little about oil; skip it for energy.
        if not (asset_class == "commodity" and base_symbol in ENERGY_COMMODITIES):
            strategies["yield_curve"] = _yield_curve(context)
    if asset_class == "crypto":
        strategies["funding_regime"] = _funding_regime(context)
        strategies["oi_momentum"] = _oi_momentum(context, timestamps, closes)
        strategies["long_short"] = _long_short(context)
        strategies["liquidations"] = _liquidations(context)
    if asset_class == "stock":
        strategies["relative_strength"] = _relative_strength(context)
        strategies["event_risk"] = _event_risk(context)

    return {
        "generated_at": base["generated_at"],
        "candles": base["candles"],
        "outlook": compute_outlook(strategies),
        "strategies": strategies,
    }
