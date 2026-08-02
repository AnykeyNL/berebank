"""KimiK3 analysis: a single direction outlook blended from TA strategies.

Reuses the five strategy signals from ``analysis.analyze`` (single source of
truth for indicator math), adds a sixth signal based on the Average
Directional Index (ADX), and blends all votes into one composite outlook:
a direction (bullish/bearish/neutral), a -100..+100 score, a confidence
level and per-strategy contributions so users can see exactly why.

Weighting is regime-aware: when the ADX shows a strong trend (>= 25) the
trend-following strategies (trend, MACD) count double; in a ranging market
(ADX < 20) the mean-reversion strategies (RSI, Bollinger) count double.
"""
from __future__ import annotations

from . import analysis

TREND_STRENGTH_EXPLANATION = (
    "The Average Directional Index (ADX, 14 bars) measures how strong the "
    "current trend is, regardless of direction: below 20 the market is "
    "ranging (no trend), above 25 the trend is strong. The +DI and -DI lines "
    "show which side drives it: +DI above -DI means buyers push harder."
)

# Fixed vote order so contributions render consistently.
STRATEGY_ORDER = ["trend", "rsi", "macd", "volatility", "levels_volume", "trend_strength"]

# Regime-aware weighting sets.
_TREND_FOLLOWING = {"trend", "macd"}
_MEAN_REVERSION = {"rsi", "volatility"}

# Score thresholds on the -100..+100 scale.
_BULLISH_AT = 20
_BEARISH_AT = -20

# ADX regime thresholds.
_ADX_TRENDING = 25.0
_ADX_RANGING = 20.0


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


def _insufficient() -> dict:
    return {
        "signal": "none",
        "reason": {"code": "insufficient_data", "params": {}},
        "explanation": TREND_STRENGTH_EXPLANATION,
        "values": {},
        "series": {},
    }


def _trend_strength(timestamps, highs, lows, closes, start) -> dict:
    """ADX-based trend strength/direction signal (6th strategy)."""
    if len(closes) < 30:
        return _insufficient()
    adx_values, plus_di, minus_di = adx(highs, lows, closes)
    current, pdi, mdi = adx_values[-1], plus_di[-1], minus_di[-1]
    if current is None or pdi is None or mdi is None:
        return _insufficient()

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
        "explanation": TREND_STRENGTH_EXPLANATION,
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


def compute_outlook(strategies: dict, context: dict | None = None) -> dict:
    """Blend strategy signals into one direction outlook.

    Each strategy votes +1 (bullish), -1 (bearish) or 0 (neutral); "none"
    strategies are excluded. The score is the weighted vote share scaled to
    -100..+100; confidence reflects what fraction of active strategies
    agrees with the resulting direction.

    Optional ``context`` (Twelve Data supplementary data) adjusts weighting
    near earnings, applies macro regime nudges, and uses insider activity as
    a tie-breaker when the technical score is close to neutral.
    """
    ts_values = strategies.get("trend_strength", {}).get("values", {})
    adx_raw = ts_values.get("adx")
    regime = regime_for(float(adx_raw) if adx_raw else None)
    earnings_near = bool(context and context.get("earnings_near"))

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
            ):
                weight = 2.0
        contributions.append({"strategy": key, "signal": signal, "weight": weight})

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

    macro_regime = context.get("macro_regime") if context else None
    if macro_regime == "risk_off" and -20 <= score <= 20:
        score = max(-100, score - 15)
    elif macro_regime == "risk_on" and -20 <= score <= 20:
        score = min(100, score + 15)

    insider = context.get("insider_signal") if context else None
    if insider in ("bullish", "bearish") and abs(score) <= 15:
        score = min(100, score + 12) if insider == "bullish" else max(-100, score - 12)

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
    if macro_regime and macro_regime != "neutral":
        reason_params["macro_regime"] = macro_regime
    return {
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "regime": regime,
        "reason": {
            "code": f"outlook_{direction}",
            "params": reason_params,
        },
        "contributions": contributions,
    }


def analyze_kimi(
    candles: list[list],
    display_count: int,
    context: dict | None = None,
) -> dict:
    """KimiK3 outlook over ``candles`` (oldest first, API candle shape).

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
        "trend_strength": _trend_strength(timestamps, highs, lows, closes, start),
    }
    return {
        "generated_at": base["generated_at"],
        "candles": base["candles"],
        "outlook": compute_outlook(strategies, context),
        "strategies": strategies,
    }
