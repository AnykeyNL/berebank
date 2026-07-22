"""Technical analysis over OHLCV candles.

Pure functions over the API candle shape [timestamp_ms, open, high, low,
close, volume] (numbers serialized as strings). ``analyze`` produces the
per-strategy payload used by both the REST endpoint and the MCP tool:
each strategy yields a signal (bullish/bearish/neutral, or "none" when there
is not enough data), a structured reason (code + params so the frontend can
localize it), key values, and overlay series trimmed to the display window.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

# Extra bars fetched before the display window so indicators such as SMA-50
# are already warmed up on the first visible bar.
WARMUP_BARS = 60

# One-line English explanations, aimed at MCP clients relaying how each
# strategy works. The web frontend uses its own localized texts.
EXPLANATIONS = {
    "trend": (
        "Compares short- and long-term moving averages (SMA-20/50, EMA-12/26): "
        "when the faster average and the price are above the slower average the "
        "trend is up; a fast average crossing the slow one signals a trend change "
        "(golden/death cross)."
    ),
    "rsi": (
        "RSI-14 measures momentum on a 0-100 scale: above 70 the asset is "
        "overbought (pullback risk), below 30 oversold (bounce candidate), "
        "in between it just shows momentum direction."
    ),
    "macd": (
        "MACD (12, 26, 9) tracks the gap between two EMAs: the MACD line crossing "
        "above its signal line is bullish momentum, crossing below is bearish, and "
        "the histogram shows whether that momentum is growing or fading."
    ),
    "volatility": (
        "Bollinger Bands (20, 2 sigma) mark a typical price envelope: price at a "
        "band means it is stretched, a narrow band (squeeze) often precedes a "
        "breakout. ATR-14 shows the typical move per bar and suggests a stop-loss "
        "distance."
    ),
    "levels_volume": (
        "Clusters recent swing highs/lows into support and resistance levels and "
        "checks whether price is near one, plus whether recent volume runs above "
        "or below average (high-volume moves are more trustworthy)."
    ),
}


# ---- basic indicator math (aligned lists; None while not enough history) ----

def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period  # seed with SMA
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = prev + k * (values[i] - prev)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        # Wilder smoothing
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    defined = [v for v in line if v is not None]
    signal_defined = ema(defined, signal_period) if defined else []
    signal_line: list[float | None] = [None] * len(line)
    offset = len(line) - len(defined)
    for i, v in enumerate(signal_defined):
        signal_line[offset + i] = v
    hist: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(line, signal_line)
    ]
    return line, signal_line, hist


def bollinger(
    closes: list[float], period: int = 20, num_std: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    middle = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        assert mean is not None
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return middle, upper, lower


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14,
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period + 1, n):
        # Wilder smoothing over true ranges (trs is offset by one vs closes)
        prev = (prev * (period - 1) + trs[i - 1]) / period
        out[i] = prev
    return out


def pivot_levels(
    highs: list[float],
    lows: list[float],
    *,
    window: int = 3,
    tolerance: float = 0.005,
    max_levels: int = 8,
) -> list[dict]:
    """Cluster swing highs/lows into price levels.

    A bar is a pivot high (low) when its high (low) is the extreme of the
    surrounding ``window`` bars on both sides. Pivot prices within
    ``tolerance`` (relative) of each other merge into one level; levels
    touched more often are stronger.
    """
    pivots: list[float] = []
    n = len(highs)
    for i in range(window, n - window):
        seg_h = highs[i - window : i + window + 1]
        seg_l = lows[i - window : i + window + 1]
        if highs[i] == max(seg_h):
            pivots.append(highs[i])
        if lows[i] == min(seg_l):
            pivots.append(lows[i])
    if not pivots:
        return []

    pivots.sort()
    clusters: list[list[float]] = [[pivots[0]]]
    for p in pivots[1:]:
        ref = clusters[-1][0]
        if ref > 0 and (p - ref) / ref <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    levels = [
        {"price": sum(c) / len(c), "strength": len(c)}
        for c in clusters
    ]
    levels.sort(key=lambda lv: (-lv["strength"], -lv["price"]))
    levels = levels[:max_levels]
    levels.sort(key=lambda lv: lv["price"])
    return levels


def last_cross(
    a: list[float | None], b: list[float | None], lookback: int = 5,
) -> tuple[str, int] | None:
    """Most recent crossing of series ``a`` over/under ``b`` within
    ``lookback`` bars: ("up"|"down", bars_ago), newest first."""
    n = min(len(a), len(b))
    for bars_ago in range(0, lookback):
        i = n - 1 - bars_ago
        if i < 1:
            break
        a1, b1, a0, b0 = a[i], b[i], a[i - 1], b[i - 1]
        if None in (a1, b1, a0, b0):
            continue
        if a0 <= b0 and a1 > b1:
            return ("up", bars_ago)
        if a0 >= b0 and a1 < b1:
            return ("down", bars_ago)
    return None


# ---- serialization helpers ----

def _s(value: float | None) -> str | None:
    """Format a float as a compact decimal string (API convention)."""
    if value is None:
        return None
    if value == 0:
        return "0"
    # 10 significant digits covers EUR prices from sub-cent tokens to BTC.
    text = f"{value:.10g}"
    if "e" in text or "E" in text:
        text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text


def _series(timestamps: list[int], values: list[float | None], start: int) -> list[list]:
    return [
        [timestamps[i], _s(values[i])]
        for i in range(start, len(timestamps))
    ]


def _insufficient(strategy: str) -> dict:
    return {
        "signal": "none",
        "reason": {"code": "insufficient_data", "params": {}},
        "explanation": EXPLANATIONS[strategy],
        "values": {},
        "series": {},
    }


# ---- strategies ----

def _trend(timestamps, closes, start) -> dict:
    if len(closes) < 51:
        return _insufficient("trend")
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    price = closes[-1]
    fast, slow = sma20[-1], sma50[-1]

    cross = last_cross(sma20, sma50, lookback=5)
    if cross is not None:
        direction, bars_ago = cross
        code = "golden_cross" if direction == "up" else "death_cross"
        signal = "bullish" if direction == "up" else "bearish"
        reason = {"code": code, "params": {"bars_ago": bars_ago}}
    elif fast is not None and slow is not None and price > slow and fast > slow:
        signal = "bullish"
        reason = {"code": "uptrend", "params": {"sma_fast": _s(fast), "sma_slow": _s(slow)}}
    elif fast is not None and slow is not None and price < slow and fast < slow:
        signal = "bearish"
        reason = {"code": "downtrend", "params": {"sma_fast": _s(fast), "sma_slow": _s(slow)}}
    else:
        signal = "neutral"
        reason = {"code": "mixed_trend", "params": {"sma_fast": _s(fast), "sma_slow": _s(slow)}}

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["trend"],
        "values": {
            "price": _s(price),
            "sma20": _s(sma20[-1]),
            "sma50": _s(sma50[-1]),
            "ema12": _s(ema12[-1]),
            "ema26": _s(ema26[-1]),
        },
        "series": {
            "sma20": _series(timestamps, sma20, start),
            "sma50": _series(timestamps, sma50, start),
            "ema12": _series(timestamps, ema12, start),
            "ema26": _series(timestamps, ema26, start),
        },
    }


def _rsi_strategy(timestamps, closes, start) -> dict:
    if len(closes) < 15:
        return _insufficient("rsi")
    values = rsi(closes, 14)
    current = values[-1]
    if current is None:
        return _insufficient("rsi")

    prev = next((v for v in reversed(values[:-3]) if v is not None), current)
    direction = "rising" if current >= prev else "falling"
    if current > 70:
        signal = "bearish"
        reason = {"code": "overbought", "params": {"rsi": _s(current)}}
    elif current < 30:
        signal = "bullish"
        reason = {"code": "oversold", "params": {"rsi": _s(current)}}
    else:
        signal = "neutral"
        reason = {"code": "rsi_neutral", "params": {"rsi": _s(current), "direction": direction}}

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["rsi"],
        "values": {"rsi": _s(current), "direction": direction},
        "series": {"rsi": _series(timestamps, values, start)},
    }


def _macd_strategy(timestamps, closes, start) -> dict:
    if len(closes) < 35:
        return _insufficient("macd")
    line, signal_line, hist = macd(closes)
    m, s, h = line[-1], signal_line[-1], hist[-1]
    if m is None or s is None or h is None:
        return _insufficient("macd")

    prev_h = next((v for v in reversed(hist[:-1]) if v is not None), h)
    hist_direction = "growing" if abs(h) >= abs(prev_h) else "fading"
    cross = last_cross(line, signal_line, lookback=5)
    if cross is not None:
        direction, bars_ago = cross
        code = "macd_bull_cross" if direction == "up" else "macd_bear_cross"
        signal = "bullish" if direction == "up" else "bearish"
        reason = {"code": code, "params": {"bars_ago": bars_ago}}
    elif m > s and h >= prev_h:
        signal = "bullish"
        reason = {"code": "macd_above", "params": {"histogram": _s(h)}}
    elif m < s and h <= prev_h:
        signal = "bearish"
        reason = {"code": "macd_below", "params": {"histogram": _s(h)}}
    else:
        signal = "neutral"
        reason = {"code": "macd_flat", "params": {"histogram": _s(h)}}

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["macd"],
        "values": {
            "macd": _s(m),
            "signal": _s(s),
            "histogram": _s(h),
            "histogram_direction": hist_direction,
        },
        "series": {
            "macd": _series(timestamps, line, start),
            "signal": _series(timestamps, signal_line, start),
            "histogram": _series(timestamps, hist, start),
        },
    }


def _volatility(timestamps, highs, lows, closes, start) -> dict:
    if len(closes) < 21:
        return _insufficient("volatility")
    middle, upper, lower = bollinger(closes)
    atr_values = atr(highs, lows, closes)
    price = closes[-1]
    mid, up, low_band = middle[-1], upper[-1], lower[-1]
    if mid is None or up is None or low_band is None:
        return _insufficient("volatility")
    current_atr = atr_values[-1]

    bandwidths = [
        (u - l) / m
        for m, u, l in zip(middle[start:], upper[start:], lower[start:])
        if m is not None and u is not None and l is not None and m != 0
    ]
    bandwidth = (up - low_band) / mid if mid != 0 else 0.0
    squeeze = False
    if len(bandwidths) >= 10:
        sorted_bw = sorted(bandwidths)
        squeeze = bandwidth <= sorted_bw[max(0, len(sorted_bw) // 5 - 1)]

    atr_pct = (current_atr / price * 100) if current_atr is not None and price != 0 else None
    suggested_stop = price - 2 * current_atr if current_atr is not None else None

    if price >= up:
        signal = "bearish"
        reason = {"code": "stretched_high", "params": {"price": _s(price), "upper": _s(up)}}
    elif price <= low_band:
        signal = "bullish"
        reason = {"code": "stretched_low", "params": {"price": _s(price), "lower": _s(low_band)}}
    elif squeeze:
        signal = "neutral"
        reason = {"code": "squeeze", "params": {"bandwidth_pct": _s(bandwidth * 100)}}
    else:
        signal = "neutral"
        reason = {"code": "vol_normal", "params": {"bandwidth_pct": _s(bandwidth * 100)}}

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["volatility"],
        "values": {
            "bb_upper": _s(up),
            "bb_middle": _s(mid),
            "bb_lower": _s(low_band),
            "atr": _s(current_atr),
            "atr_pct": _s(atr_pct),
            "suggested_stop": _s(suggested_stop),
        },
        "series": {
            "bb_upper": _series(timestamps, upper, start),
            "bb_middle": _series(timestamps, middle, start),
            "bb_lower": _series(timestamps, lower, start),
        },
    }


def _levels_volume(timestamps, highs, lows, closes, volumes, start) -> dict:
    display_len = len(closes) - start
    if display_len < 20:
        return _insufficient("levels_volume")
    price = closes[-1]
    levels = pivot_levels(highs[start:], lows[start:])

    supports = [lv for lv in levels if lv["price"] < price]
    resistances = [lv for lv in levels if lv["price"] >= price]
    support = supports[-1]["price"] if supports else None
    resistance = resistances[0]["price"] if resistances else None
    support_dist_pct = ((price - support) / price * 100) if support and price else None
    resistance_dist_pct = ((resistance - price) / price * 100) if resistance and price else None

    vols = volumes[start:]
    avg_vol = sum(vols) / len(vols) if vols else 0.0
    recent = vols[-5:] if len(vols) >= 5 else vols
    recent_avg = sum(recent) / len(recent) if recent else 0.0
    volume_ratio = (recent_avg / avg_vol) if avg_vol > 0 else None
    if volume_ratio is None:
        volume_state = "unknown"
    elif volume_ratio >= 1.25:
        volume_state = "above_average"
    elif volume_ratio <= 0.75:
        volume_state = "below_average"
    else:
        volume_state = "average"

    near_pct = 1.5
    if not levels:
        signal = "neutral"
        reason = {"code": "no_levels", "params": {}}
    elif support_dist_pct is not None and support_dist_pct <= near_pct:
        signal = "bullish"
        reason = {"code": "near_support", "params": {"support": _s(support), "volume_state": volume_state}}
    elif resistance_dist_pct is not None and resistance_dist_pct <= near_pct:
        signal = "bearish"
        reason = {"code": "near_resistance", "params": {"resistance": _s(resistance), "volume_state": volume_state}}
    else:
        signal = "neutral"
        reason = {
            "code": "between_levels",
            "params": {"support": _s(support), "resistance": _s(resistance), "volume_state": volume_state},
        }

    return {
        "signal": signal,
        "reason": reason,
        "explanation": EXPLANATIONS["levels_volume"],
        "values": {
            "support": _s(support),
            "resistance": _s(resistance),
            "support_dist_pct": _s(support_dist_pct),
            "resistance_dist_pct": _s(resistance_dist_pct),
            "volume_ratio": _s(volume_ratio),
            "volume_state": volume_state,
        },
        "series": {},
        "levels": [
            {"price": _s(lv["price"]), "strength": lv["strength"]}
            for lv in levels
        ],
    }


# ---- entry point ----

def analyze(candles: list[list], display_count: int) -> dict:
    """Run all strategies over ``candles`` (oldest first, API candle shape).

    ``display_count`` is the number of trailing bars that make up the
    requested window; earlier bars are indicator warm-up only and are not
    included in the returned candles/series.
    """
    timestamps = [int(c[0]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    start = max(0, len(candles) - display_count)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candles": candles[start:],
        "strategies": {
            "trend": _trend(timestamps, closes, start),
            "rsi": _rsi_strategy(timestamps, closes, start),
            "macd": _macd_strategy(timestamps, closes, start),
            "volatility": _volatility(timestamps, highs, lows, closes, start),
            "levels_volume": _levels_volume(timestamps, highs, lows, closes, volumes, start),
        },
    }
