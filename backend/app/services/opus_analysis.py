"""Opus analysis: turn calibrated features into buy and sell recommendations.

Opus answers a different question than the other engines. They ask "what do my
indicators say about this market?"; Opus asks "of everything I could hold for
the next day to four weeks, how attractive is this one, and is the edge bigger
than the fees I would pay?".

The scoring chain:

1. Feature z-scores — the market's rank within its peer group today
   (:mod:`opus_features`), or against its own history when no peer group is
   available (the walk-forward track record replays a single market).
2. Composite — the weighted sum of those z-scores using the weights learned in
   :mod:`opus_calibration`, rescaled by the share of weight actually available.
3. Expected return — the calibrated score-to-return map gives the peer-relative
   alpha, added to the regime's average peer return.
4. Net edge — expected return minus real Bitvavo fees: two legs for a buy
   (enter and exit), one leg for selling something already held.
5. Conviction — net edge divided by the expected move over the horizon, i.e. an
   information ratio, which is what ``buy_score`` and ``sell_score`` express.

Pure functions over candle lists and plain dicts: no database, no request
state, so everything here is directly unit-testable and safe to reuse from the
nightly job and the request path alike.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import analysis
from . import opus_calibration as calibration
from .opus_features import (
    FEATURE_KEYS,
    MarketSeries,
    extract_market_features,
    time_series_z,
)

# Base Bitvavo Category A fees (percent), used when no tier is supplied.
DEFAULT_TAKER_PCT = 0.25
DEFAULT_MAKER_PCT = 0.15

# Peer-group drift reaches a market through its beta, capped so an extreme
# beta estimate cannot multiply the drift into fantasy.
BETA_CAP = 2.0

# Score mapping: composite standard deviations to the -100..+100 gauge.
SCORE_PER_SIGMA = 40.0
FALLBACK_COMPOSITE_SCALE = 0.5  # typical dispersion without a calibration

# Direction thresholds on the gauge, matching the other analysis pages.
BULLISH_AT = 20
BEARISH_AT = -20

# An information ratio of this size over the horizon scores 100.
CONVICTION_FULL = 0.5

# Volatility guards, in percent of daily volatility (scaled by sqrt(horizon)).
# The floor keeps a rounding error from dividing into infinite conviction; the
# low-volatility cut-off drops instruments that cannot move enough to pay a fee
# over these horizons — euro stablecoins and money-market-like bond funds.
MIN_MOVE_DAILY_PCT = 0.5
LOW_VOL_DAILY_PCT = 0.35

# Action thresholds on the 0..100 recommendation scores.
STRONG_AT = 60
ACT_AT = 30

# Confidence from the share of weight agreeing with the verdict.
HIGH_AGREEMENT = 0.68
MEDIUM_AGREEMENT = 0.56

# Contribution share below which a feature is reported as neutral.
NEUTRAL_SHARE = 0.01

# Suggested stop distance in ATR units.
STOP_ATR_MULTIPLE = 2.0

_RANK_SCALE = math.sqrt(12.0)

HORIZON_DAYS = {"1d": 1, "1w": 5, "4w": 21}

EXPLANATIONS: dict[str, str] = {
    "mom_21": (
        "One-month price change divided by the market's own volatility, so a "
        "calm 5% move counts for more than a wild 5% move. Ranked against "
        "peers: a high rank means this market is among the strongest movers of "
        "its group right now."
    ),
    "mom_63": (
        "The same volatility-adjusted momentum measured over three months. It "
        "captures the slower trend that tends to persist over multi-week "
        "horizons."
    ),
    "accel": (
        "One-month momentum minus three-month momentum: is the trend speeding "
        "up or fading? Acceleration often leads the price."
    ),
    "rev_5": (
        "Short-term reversal. The last week's move is inverted, because over a "
        "few days the market's recent losers tend to bounce and its recent "
        "winners tend to give some back — the opposite of the monthly trend."
    ),
    "rev_1": (
        "Yesterday's move, inverted and volatility-scaled: a one-day overreaction "
        "signal that matters most for the shortest horizon."
    ),
    "ma_dist": (
        "Distance between the price and its 50-day average, measured in average "
        "daily ranges (ATR). It says how stretched the price is relative to its "
        "own normal swing size."
    ),
    "adx_dir": (
        "Trend strength (ADX) signed by trend direction (+DI versus -DI). High "
        "values mean a strong, directional market; near zero means the price is "
        "chopping sideways."
    ),
    "rsi_dev": (
        "RSI-14 expressed as a deviation from the neutral 50 line. Extremes tend "
        "to mean-revert over days, so a very high reading is not automatically "
        "good news."
    ),
    "bb_pos": (
        "Where the price sits inside its Bollinger Bands, in standard "
        "deviations from the 20-day mean."
    ),
    "range_pos": (
        "Position inside the 20-day high-low range, from -1 at the low to +1 at "
        "the high. Near the top means a breakout is close; near the bottom "
        "means the market is testing support."
    ),
    "vol_ratio": (
        "This week's volatility divided by this month's. Expanding volatility "
        "usually means rising risk rather than rising reward."
    ),
    "vol_level": (
        "The market's own daily volatility. Ranked across peers this captures "
        "the low-volatility effect: calmer markets historically deliver better "
        "return per unit of risk."
    ),
    "dd_63": (
        "How far the price is below its three-month high. Shallow drawdowns "
        "signal strength; deep ones signal damage that takes time to repair."
    ),
    "vol_z": (
        "Recent trading volume versus its monthly norm. A volume surge shows "
        "the move is backed by participation."
    ),
    "turnover": (
        "Median daily euro turnover, a liquidity measure. It also matters "
        "practically: thin markets are expensive to enter and exit."
    ),
    "beta_mkt": (
        "Sensitivity to the market's own asset-class index over three months. "
        "High beta amplifies whatever the broad market does next."
    ),
    "corr_mkt": (
        "Correlation with the asset-class index. Low correlation makes a "
        "position a genuine diversifier rather than another copy of the same "
        "bet."
    ),
    "resid_mom": (
        "Residual momentum: the part of the last month's move that the asset "
        "class as a whole does not explain. It isolates what is specific to "
        "this market and is historically more reliable than raw momentum."
    ),
    "beta_vix": (
        "How this market has moved when the VIX volatility index moved, over "
        "the last three months. Negative sensitivity marks assets that suffer "
        "when fear rises; positive marks the rare ones that benefit."
    ),
    "beta_rate": (
        "Sensitivity to changes in the US 10-year treasury yield. Long-duration "
        "assets — growth stocks, gold, bonds — react most, so this says who is "
        "exposed if rates move."
    ),
    "beta_fng": (
        "Sensitivity to shifts in the Crypto Fear & Greed index. High values "
        "mark the markets that live and die by retail sentiment."
    ),
    "beta_stable": (
        "Sensitivity to growth in the total stablecoin supply, the closest thing "
        "crypto has to a money-supply measure. Assets that respond most tend to "
        "lead when new money arrives."
    ),
    "funding": (
        "Average perpetual futures funding rate across exchanges. Strongly "
        "positive funding means crowded, expensive long positioning, which "
        "tends to precede flushes."
    ),
}


def _to_percent_string(value: float | None, digits: int = 2) -> str | None:
    if value is None:
        return None
    return f"{value:.{digits}f}"


def _percentile(z: float) -> float:
    return max(0.0, min(1.0, z / _RANK_SCALE + 0.5))


def _percentile_reason(percentile: float, weight: float) -> dict:
    if not weight:
        return {"code": "opus_no_weight", "params": {"percentile": round(percentile * 100)}}
    if percentile >= 0.9:
        code = "opus_percentile_top"
    elif percentile >= 0.65:
        code = "opus_percentile_high"
    elif percentile > 0.35:
        code = "opus_percentile_mid"
    elif percentile > 0.1:
        code = "opus_percentile_low"
    else:
        code = "opus_percentile_bottom"
    return {"code": code, "params": {"percentile": round(percentile * 100)}}


def round_trip_fee_pct(taker_pct: float = DEFAULT_TAKER_PCT) -> float:
    """Cost of entering and later exiting a position, in percent."""
    return 2.0 * taker_pct


def horizon_bars(horizon: str) -> int:
    return HORIZON_DAYS.get(horizon, HORIZON_DAYS["1w"])


def build_feature_report(
    z_scores: dict[str, float],
    weights: dict[str, float],
    raw_values: dict[str, float] | None = None,
    ic: dict[str, dict] | None = None,
) -> tuple[dict[str, dict], list[dict], float, float]:
    """Per-feature detail, template-shaped contributions and vote weights.

    Returns ``(strategies, contributions, bullish_weight, bearish_weight)``.
    """
    total_weight = sum(abs(w) for w in weights.values()) or 1.0
    strategies: dict[str, dict] = {}
    contributions: list[dict] = []
    bullish_weight = 0.0
    bearish_weight = 0.0

    for feature in FEATURE_KEYS:
        weight = weights.get(feature, 0.0)
        z = z_scores.get(feature)
        explanation = EXPLANATIONS.get(feature, "")
        if z is None:
            strategies[feature] = {
                "signal": "none",
                "reason": {"code": "insufficient_data", "params": {}},
                "explanation": explanation,
                "values": {"weight": _to_percent_string(abs(weight) * 100)},
                "series": {},
            }
            continue
        contribution = weight * z
        share = contribution / total_weight
        if share > NEUTRAL_SHARE:
            signal = "bullish"
            bullish_weight += abs(weight)
        elif share < -NEUTRAL_SHARE:
            signal = "bearish"
            bearish_weight += abs(weight)
        else:
            signal = "neutral"
        percentile = _percentile(z)
        feature_ic = (ic or {}).get(feature) or {}
        values = {
            "z": _to_percent_string(z),
            "percentile": str(round(percentile * 100)),
            "weight": _to_percent_string(abs(weight) * 100),
            "contribution": _to_percent_string(share * 100),
        }
        if raw_values and feature in raw_values:
            values["value"] = _to_percent_string(raw_values[feature], 4)
        if feature_ic.get("mean") is not None:
            values["ic"] = _to_percent_string(float(feature_ic["mean"]) * 100)
            values["ic_days"] = str(int(feature_ic.get("days") or 0))
        strategies[feature] = {
            "signal": signal,
            "reason": _percentile_reason(percentile, weight),
            "explanation": explanation,
            "values": values,
            "series": {},
        }
        if weight:
            contributions.append({
                "strategy": feature,
                "signal": signal,
                "weight": round(abs(weight) * 100, 1),
            })

    contributions.sort(key=lambda item: item["weight"], reverse=True)
    return strategies, contributions, bullish_weight, bearish_weight


def compute_outlook(
    composite: float,
    composite_scale: float,
    bullish_weight: float,
    bearish_weight: float,
    contributions: list[dict],
    *,
    regime: str = "all",
    weights_learned: bool = False,
) -> dict:
    """Direction, gauge score and confidence from a composite score."""
    scale = composite_scale or FALLBACK_COMPOSITE_SCALE
    normalized = composite / scale if scale else 0.0
    score = int(max(-100, min(100, round(normalized * SCORE_PER_SIGMA))))

    if score >= BULLISH_AT:
        direction = "bullish"
    elif score <= BEARISH_AT:
        direction = "bearish"
    else:
        direction = "neutral"

    active_weight = bullish_weight + bearish_weight
    if not contributions or active_weight <= 0:
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

    if direction == "bullish":
        agreement = bullish_weight / active_weight
    elif direction == "bearish":
        agreement = bearish_weight / active_weight
    else:
        agreement = 1.0 - abs(bullish_weight - bearish_weight) / active_weight

    if agreement >= HIGH_AGREEMENT and weights_learned:
        confidence = "high"
    elif agreement >= MEDIUM_AGREEMENT:
        confidence = "medium"
    else:
        confidence = "low"

    counts = {
        "bullish": sum(1 for c in contributions if c["signal"] == "bullish"),
        "bearish": sum(1 for c in contributions if c["signal"] == "bearish"),
        "neutral": sum(1 for c in contributions if c["signal"] == "neutral"),
    }
    return {
        "direction": direction,
        "score": score,
        "buy_score": max(0, score),
        "sell_score": max(0, -score),
        "confidence": confidence,
        "regime": regime,
        "reason": {"code": f"outlook_{direction}", "params": {**counts, "total": len(contributions)}},
        "contributions": contributions,
    }


def recommendation_from_edge(
    expected_return_pct: float | None,
    expected_vol_pct: float | None,
    *,
    bars: int = 5,
    taker_pct: float = DEFAULT_TAKER_PCT,
    maker_pct: float = DEFAULT_MAKER_PCT,
    held: bool = False,
) -> dict:
    """Fee-aware buy/sell recommendation for one market.

    The gauge score, direction, both recommendation scores and the action all
    come from the same two numbers — expected return and expected move — so
    they can never contradict each other. Fees decide what is worth acting on:
    a buy must clear a round trip, while selling only has to clear the single
    exit fee because the alternative is holding cash.

    Both fee tiers are evaluated. Over a one-day or one-week horizon the edge is
    often smaller than two taker legs but larger than two maker legs, in which
    case the trade is only worth doing with limit orders — and Opus says so
    instead of silently calling it a hold.
    """
    buy_fee = round_trip_fee_pct(taker_pct)
    limit_fee = round_trip_fee_pct(maker_pct)
    sell_fee = taker_pct
    if expected_return_pct is None or expected_vol_pct is None:
        return {
            "action": "hold",
            "score": None,
            "direction": None,
            "expected_return_pct": _to_percent_string(expected_return_pct),
            "fee_pct": _to_percent_string(buy_fee),
            "limit_fee_pct": _to_percent_string(limit_fee),
            "net_edge_pct": None,
            "net_edge_limit_pct": None,
            "sell_edge_pct": None,
            "conviction": None,
            "buy_score": 0,
            "sell_score": 0,
            "low_volatility": False,
            "requires_limit_order": False,
            "tradable_edge": False,
        }

    # Floor the expected move: near-zero volatility instruments (stablecoins
    # above all) would otherwise divide a rounding error into infinite
    # conviction.
    floor = MIN_MOVE_DAILY_PCT * math.sqrt(bars)
    move = max(expected_vol_pct, floor)
    low_volatility = expected_vol_pct < LOW_VOL_DAILY_PCT * math.sqrt(bars)

    net_buy = expected_return_pct - buy_fee
    net_limit = expected_return_pct - limit_fee
    net_sell = -expected_return_pct - sell_fee
    conviction = expected_return_pct / move
    best_buy = max(net_buy, net_limit)
    requires_limit_order = net_buy <= 0 < net_limit

    score = int(max(-100, min(100, round(100 * conviction / CONVICTION_FULL))))
    buy_score = int(round(100 * max(0.0, min(1.0, best_buy / move / CONVICTION_FULL))))
    sell_score = int(round(100 * max(0.0, min(1.0, net_sell / move / CONVICTION_FULL))))

    if buy_score >= STRONG_AT:
        action = "strong_buy"
    elif buy_score >= ACT_AT:
        action = "buy"
    elif sell_score >= STRONG_AT:
        action = "sell"
    elif sell_score >= ACT_AT:
        action = "reduce"
    else:
        action = "hold"
    if score >= BULLISH_AT:
        direction = "bullish"
    elif score <= BEARISH_AT:
        direction = "bearish"
    else:
        direction = "neutral"

    if low_volatility:
        # Euro stablecoins and money-market-like funds: whatever expected return
        # comes out of the model is estimation noise on a flat line, not a
        # direction worth paying a fee for.
        action = "hold"
        score = 0
        direction = "neutral"
        buy_score = 0
        sell_score = 0

    return {
        "action": action,
        "score": score,
        "direction": direction,
        "expected_return_pct": _to_percent_string(expected_return_pct),
        "fee_pct": _to_percent_string(buy_fee),
        "limit_fee_pct": _to_percent_string(limit_fee),
        "net_edge_pct": _to_percent_string(net_buy),
        "net_edge_limit_pct": _to_percent_string(net_limit),
        "sell_edge_pct": _to_percent_string(net_sell),
        "conviction": _to_percent_string(conviction),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "low_volatility": low_volatility,
        "requires_limit_order": requires_limit_order and not low_volatility,
        "tradable_edge": best_buy > 0 or (held and net_sell > 0),
    }


def merge_edge_into_outlook(outlook: dict, recommendation: dict) -> dict:
    """Let a calibrated expected return drive the verdict.

    With a calibrated return the gauge shows expected move per unit of risk and
    the direction follows from it. Without one — a young install, or the
    single-market walk-forward replay — the peer-rank composite is all there is,
    so the outlook is left as computed.
    """
    if recommendation.get("score") is None:
        return outlook
    return {
        **outlook,
        "score": recommendation["score"],
        "direction": recommendation["direction"],
        "buy_score": recommendation["buy_score"],
        "sell_score": recommendation["sell_score"],
        "reason": {
            **outlook["reason"],
            "code": f"outlook_{recommendation['direction']}",
        },
    }


def expected_move_pct(series: MarketSeries, index: int, bars: int) -> float | None:
    """Expected absolute move over ``bars`` days from recent daily volatility."""
    daily = series.features["vol_level"][index]
    if not daily:
        return None
    return daily * math.sqrt(bars) * 100


def suggested_stop(candles: list[list]) -> tuple[str | None, str | None]:
    """Stop-loss suggestion two ATRs below the last close."""
    try:
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
    except (IndexError, TypeError, ValueError):
        return None, None
    atr_values = analysis.atr(highs, lows, closes, 14)
    atr14 = atr_values[-1] if atr_values else None
    if not atr14 or not closes:
        return None, None
    price = closes[-1]
    if price <= 0:
        return None, None
    distance = STOP_ATR_MULTIPLE * atr14
    return (
        f"{distance / price * 100:.2f}",
        f"{max(price - distance, 0.0):.10g}",
    )


def score_market(
    z_scores: dict[str, float],
    payload: dict | None,
    *,
    regime: str = "all",
    raw_values: dict[str, float] | None = None,
) -> dict:
    """Composite score, outlook and expected alpha for one market.

    ``payload`` is the calibration record for the market's peer group, horizon
    and regime; without it the documented prior weights are used and no
    expected return is produced.
    """
    if payload:
        weights = {k: float(v) for k, v in (payload.get("weights") or {}).items()}
        weights_learned = bool(payload.get("weights_learned"))
        composite_scale = float(payload.get("composite_scale") or 0.0)
        ic = payload.get("ic") or {}
    else:
        total = sum(abs(w) for w in calibration.PRIOR_WEIGHTS.values()) or 1.0
        weights = {k: v / total for k, v in calibration.PRIOR_WEIGHTS.items()}
        weights_learned = False
        composite_scale = 0.0
        ic = {}

    composite, used_weight = calibration.composite_score(z_scores, weights)
    strategies, contributions, bullish, bearish = build_feature_report(
        z_scores, weights, raw_values, ic
    )
    outlook = compute_outlook(
        composite,
        composite_scale,
        bullish,
        bearish,
        contributions,
        regime=regime,
        weights_learned=weights_learned,
    )

    alpha_pct = calibration.expected_alpha_pct(payload, composite) if payload else None
    drift_pct = None
    if payload:
        drift_pct = (payload.get("market_return") or {}).get("mean_pct")

    # The peer group's drift only reaches a market through its beta. Without
    # this a euro stablecoin would inherit the whole crypto trend, and a
    # high-beta altcoin would be handed the same drift as a blue chip.
    beta = (raw_values or {}).get("beta_mkt")
    beta_used = 1.0 if beta is None else max(0.0, min(BETA_CAP, float(beta)))
    market_return_pct = None if drift_pct is None else beta_used * float(drift_pct)

    expected_return_pct = None
    if alpha_pct is not None:
        expected_return_pct = alpha_pct + float(market_return_pct or 0.0)

    return {
        "composite": composite,
        "used_weight": used_weight,
        "weights_learned": weights_learned,
        "strategies": strategies,
        "outlook": outlook,
        "alpha_pct": alpha_pct,
        "beta": beta_used,
        "group_drift_pct": drift_pct,
        "market_return_pct": market_return_pct,
        "expected_return_pct": expected_return_pct,
    }


def analyze_opus(
    candles: list[list],
    display_count: int,
    context: dict | None = None,
) -> dict:
    """Opus outlook over ``candles`` (oldest first, API candle shape).

    Two modes, both returning the same shape:

    - **Cross-sectional** (live requests): ``context`` supplies the market's
      peer-group z-scores, the calibration payload and the current regime, so
      the verdict is "how good is this versus every alternative today" and an
      expected return in percent is available.
    - **Time series** (no context): features are ranked against this market's
      own past year and the documented prior weights are used. This is the mode
      the walk-forward track record replays, so it stays deterministic and
      needs no database.
    """
    context = context or {}
    horizon = str(context.get("horizon") or calibration.DEFAULT_HORIZON)
    bars = horizon_bars(horizon)
    start = max(0, len(candles) - display_count)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    z_scores = context.get("z_scores")
    raw_values = context.get("raw_values")
    payload = context.get("calibration")
    regime = str(context.get("regime") or "all")
    mode = "cross_sectional" if z_scores else "time_series"
    expected_vol_pct = context.get("expected_vol_pct")

    if not z_scores:
        series = extract_market_features(
            str(context.get("market") or "SELF"),
            str(context.get("asset_class") or "crypto"),
            candles,
        )
        if series is None:
            return {
                "generated_at": generated_at,
                "candles": candles[start:],
                "mode": mode,
                "horizon": horizon,
                "strategies": {},
                "outlook": {
                    "direction": "none",
                    "score": 0,
                    "buy_score": 0,
                    "sell_score": 0,
                    "confidence": "low",
                    "regime": regime,
                    "reason": {"code": "outlook_no_data", "params": {}},
                    "contributions": [],
                },
                "recommendation": recommendation_from_edge(None, None),
            }
        last = len(series.days) - 1
        z_scores = time_series_z(series, last)
        raw_values = series.feature_at(last)
        if expected_vol_pct is None:
            expected_vol_pct = expected_move_pct(series, last, bars)

    scored = score_market(z_scores, payload, regime=regime, raw_values=raw_values)
    recommendation = recommendation_from_edge(
        scored["expected_return_pct"],
        expected_vol_pct,
        bars=bars,
        taker_pct=float(context.get("taker_pct") or DEFAULT_TAKER_PCT),
    )
    stop_pct, stop_price = suggested_stop(candles)
    recommendation.update({
        "horizon": horizon,
        "horizon_bars": bars,
        "expected_move_pct": _to_percent_string(expected_vol_pct),
        "market_return_pct": _to_percent_string(scored["market_return_pct"]),
        "alpha_pct": _to_percent_string(scored["alpha_pct"]),
        "suggested_stop_pct": stop_pct,
        "suggested_stop_price": stop_price,
    })

    outlook = merge_edge_into_outlook(scored["outlook"], recommendation)

    return {
        "generated_at": generated_at,
        "candles": candles[start:],
        "mode": mode,
        "horizon": horizon,
        "strategies": scored["strategies"],
        "outlook": outlook,
        "recommendation": recommendation,
        "calibration": calibration_summary(payload),
    }


# ---- ranking ----

# Tradability gates. A recommendation nobody can execute sensibly is noise.
MIN_TURNOVER_EUR = 25_000.0   # median daily euro turnover
STALE_DAYS_CRYPTO = 3
STALE_DAYS_OTHER = 6          # weekends plus a holiday

# Diversification of the highlighted basket: crypto moves as one bloc, so a
# top-10 list without caps is one bet repeated ten times.
BASKET_SIZE = 10
BASKET_GROUP_CAP = {"crypto": 4, "stock": 4, "other": 3}


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def finalize_row(
    row: dict,
    *,
    taker_pct: float = DEFAULT_TAKER_PCT,
    maker_pct: float = DEFAULT_MAKER_PCT,
    market_open: bool | None = None,
    days_since_close: int | None = None,
    held: bool = False,
) -> dict:
    """Apply fees, gates and actions to one cached score row.

    Kept out of the cached computation so a user's own fee tier and the live
    market-hours state are applied per request without recomputing features.
    """
    expected_return = _as_float(row.get("expected_return_pct"))
    expected_move = _as_float(row.get("expected_move_pct"))
    recommendation = recommendation_from_edge(
        expected_return,
        expected_move,
        bars=horizon_bars(str(row.get("horizon") or calibration.DEFAULT_HORIZON)),
        taker_pct=taker_pct,
        maker_pct=maker_pct,
        held=held,
    )

    if recommendation["score"] is None:
        # No calibrated return yet: fall back to the peer-rank gauge so the
        # ranking still orders markets sensibly, and only advise acting on the
        # clearest cases.
        score = int(row.get("score") or 0)
        recommendation["score"] = score
        recommendation["direction"] = row.get("direction") or "none"
        recommendation["buy_score"] = max(0, score)
        recommendation["sell_score"] = max(0, -score)
        if score >= BULLISH_AT * 2:
            recommendation["action"] = "buy"
        elif score <= BEARISH_AT * 2:
            recommendation["action"] = "reduce"

    turnover = _as_float(row.get("turnover_eur"))
    if row.get("asset_class") == "commodity":
        # Twelve Data spot commodities carry no volume, so there is nothing to
        # measure; fills happen against the live quote either way.
        liquidity_ok = True
    else:
        liquidity_ok = turnover is not None and turnover >= MIN_TURNOVER_EUR
    limit_days = STALE_DAYS_CRYPTO if row.get("asset_class") == "crypto" else STALE_DAYS_OTHER
    stale = days_since_close is not None and days_since_close > limit_days
    tradable_now = True if market_open is None else bool(market_open)

    if not liquidity_ok or stale:
        # Never advise buying something the simulation cannot fill cleanly;
        # exits stay allowed, since a held position still needs an opinion.
        if recommendation["action"] in ("buy", "strong_buy"):
            recommendation["action"] = "hold"
        recommendation["buy_score"] = 0

    out = {
        **row,
        **recommendation,
        "liquidity_ok": liquidity_ok,
        "stale": stale,
        "tradable": liquidity_ok and not stale and not recommendation["low_volatility"],
        "tradable_now": tradable_now,
        "suggested_order_type": (
            "limit"
            if not tradable_now or recommendation["requires_limit_order"]
            else "market"
        ),
        "held": held,
    }
    out["taker_pct"] = _to_percent_string(taker_pct)
    out["maker_pct"] = _to_percent_string(maker_pct)
    return out


def _sort_key(row: dict, side: str) -> tuple:
    """Order by recommendation strength, then edge; untradable rows last.

    Without the tradability term a day where nothing clears its fees would put
    stale or illiquid markets at the top of the board on tie-break alone.
    """
    score = row["buy_score"] if side == "buy" else row["sell_score"]
    edge = _as_float(row.get("net_edge_pct" if side == "buy" else "sell_edge_pct"))
    return (
        0 if row.get("tradable") else 1,
        -score,
        -(edge if edge is not None else -999.0),
        row["market"],
    )


def rank_rows(rows: list[dict]) -> list[dict]:
    """Assign buy and sell ranks over the whole universe, best first."""
    for side, field in (("buy", "buy_rank"), ("sell", "sell_rank")):
        ordered = sorted(rows, key=lambda row: _sort_key(row, side))
        for position, row in enumerate(ordered, start=1):
            row[field] = position
    return rows


def select_basket(rows: list[dict], size: int = BASKET_SIZE) -> list[str]:
    """Diversified shortlist of buys, capped per peer group.

    Returns the markets in basket order; callers flag them so the UI and MCP
    can show one actionable list instead of ten correlated ideas.
    """
    counts: dict[str, int] = {}
    basket: list[str] = []
    for row in sorted(rows, key=lambda row: _sort_key(row, "buy")):
        if row["buy_score"] <= 0 or row["action"] not in ("buy", "strong_buy"):
            continue
        if not row.get("liquidity_ok") or row.get("stale"):
            continue
        group = str(row.get("peer_group") or "other")
        cap = BASKET_GROUP_CAP.get(group, 3)
        if counts.get(group, 0) >= cap:
            continue
        counts[group] = counts.get(group, 0) + 1
        basket.append(row["market"])
        if len(basket) >= size:
            break
    return basket


def calibration_summary(payload: dict | None) -> dict | None:
    """User-facing provenance of the weights behind a verdict."""
    if not payload:
        return None
    walk_forward = payload.get("walk_forward") or {}
    market_return = payload.get("market_return") or {}
    return {
        "engine_version": payload.get("engine_version"),
        "peer_group": payload.get("peer_group"),
        "horizon": payload.get("horizon"),
        "regime": payload.get("regime"),
        "weights_learned": bool(payload.get("weights_learned")),
        "days": payload.get("days"),
        "from": payload.get("from"),
        "to": payload.get("to"),
        "calibrated_at": payload.get("calibrated_at"),
        "walk_forward_ic": _to_percent_string(
            None if walk_forward.get("ic") is None else float(walk_forward["ic"]) * 100
        ),
        "walk_forward_ic_days": walk_forward.get("ic_days"),
        "walk_forward_hit_rate_pct": _to_percent_string(walk_forward.get("hit_rate_pct"), 1),
        "walk_forward_samples": walk_forward.get("samples"),
        "market_return_pct": _to_percent_string(market_return.get("mean_pct")),
        "market_return_std_pct": _to_percent_string(market_return.get("std_pct")),
        "top_features": [
            {"feature": feature, "weight": _to_percent_string(abs(weight) * 100)}
            for feature, weight in sorted(
                (payload.get("weights") or {}).items(),
                key=lambda item: abs(item[1]),
                reverse=True,
            )[:5]
        ],
    }
