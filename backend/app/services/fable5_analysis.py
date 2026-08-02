"""Fable5 analysis: a fixed-weight direction gauge blended from eight signals.

Reuses the five strategy signals from ``analysis.analyze`` (single source of
truth for base indicator math) and adds three Fable5-specific strategies:
dual-horizon momentum (rate of change over 10 and 20 bars), a slow stochastic
oscillator (14, 3, 3) and ADX trend strength. All eight votes blend into one
composite outlook: a direction (bullish/bearish/neutral), a -100..+100 score
rendered as a five-zone gauge in the web app, a confidence level and
per-strategy contributions so users can see exactly why.

Unlike KimiK3's regime-aware weighting, Fable5 uses fixed importance weights
that never change with market conditions: the recipe users see is always the
same. The ADX market regime is reported as context only. Confidence is the
weighted share of active strategies agreeing with the verdict.
"""
from __future__ import annotations

from . import analysis

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
        "conditions; subdued VIX (15 or below) suggests calmer markets."
    ),
    "yield_curve": (
        "The spread between US 10-year and 2-year Treasury yields reflects "
        "growth expectations. An inverted curve (2Y above 10Y) is a classic "
        "recession warning; a steep positive spread supports risk appetite."
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


# ---- Fable5 indicator math (aligned lists; None while undefined) ----

def roc(closes: list[float], period: int) -> list[float | None]:
    """Rate of change in percent versus ``period`` bars ago."""
    out: list[float | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        prev = closes[i - period]
        if prev != 0:
            out[i] = (closes[i] / prev - 1) * 100
    return out


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


def _vix_regime(context: dict | None) -> dict:
    if not context or context.get("vix_level") is None:
        return _insufficient("vix_regime")
    vix = float(context["vix_level"])
    if vix >= 25:
        signal = "bearish"
        reason = {"code": "vix_elevated", "params": {"vix": analysis._s(vix)}}
    elif vix <= 15:
        signal = "bullish"
        reason = {"code": "vix_calm", "params": {"vix": analysis._s(vix)}}
    else:
        signal = "neutral"
        reason = {"code": "vix_neutral", "params": {"vix": analysis._s(vix)}}
    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["vix_regime"],
        "values": {"vix": analysis._s(vix)},
        "series": {},
    }


def _yield_curve(context: dict | None) -> dict:
    spread = context.get("yield_spread") if context else None
    us2y = context.get("us2y_yield") if context else None
    us10y = context.get("us10y_yield") if context else None
    if spread is not None and us2y is not None and us10y is not None:
        spread_f = float(spread)
        if spread_f < 0:
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


# ---- composite outlook ----

def compute_outlook(strategies: dict) -> dict:
    """Blend the eight strategy signals into one direction outlook.

    Each strategy votes +1 (bullish), -1 (bearish) or 0 (neutral) with its
    fixed weight; "none" strategies are excluded. The score is the weighted
    vote share scaled to -100..+100; confidence is the weighted share of
    active strategies agreeing with the resulting direction.
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
            "confidence": "low",
            "regime": regime,
            "reason": {"code": "outlook_no_data", "params": {}},
            "contributions": contributions,
        }

    vote = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}
    total_weight = sum(c["weight"] for c in active)
    weighted = sum(vote[c["signal"]] * c["weight"] for c in active)
    score = round(100 * weighted / total_weight)

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
        "confidence": confidence,
        "regime": regime,
        "reason": {
            "code": f"outlook_{direction}",
            "params": {**counts, "total": len(active)},
        },
        "contributions": contributions,
    }


# ---- entry point ----

def analyze_fable5(
    candles: list[list],
    display_count: int,
    context: dict | None = None,
) -> dict:
    """Fable5 outlook over ``candles`` (oldest first, API candle shape).

    Same contract as ``analysis.analyze``: ``display_count`` trailing bars
    form the display window, earlier bars are warm-up only.
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
        "vix_regime": _vix_regime(context),
        "yield_curve": _yield_curve(context),
    }
    return {
        "generated_at": base["generated_at"],
        "candles": base["candles"],
        "outlook": compute_outlook(strategies),
        "strategies": strategies,
    }
