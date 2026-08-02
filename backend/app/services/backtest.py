"""Walk-forward track record for an analyzer's direction outlook.

Re-runs the outlook on each historical day of the stored daily candles
and checks whether the price actually moved in the indicated direction
over the following days. Powers the "how often was this outlook right"
strip in the web app and the MCP tools. Analyzer-agnostic: the caller
passes the analyzer's entry point (e.g. ``kimi_analysis.analyze_kimi``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

FORWARD_DAYS = 5    # days ahead each outlook is checked against
MIN_SAMPLES = 10    # below this the track record is not shown
WARMUP_DAYS = 60    # history needed before the first evaluated day


def track_record(candles: list[list], analyze_fn: Callable[[list[list], int], dict]) -> dict | None:
    """Hit rate of ``analyze_fn``'s outlook over daily ``candles`` (oldest first).

    Only directional (bullish/bearish) outlooks count as samples; neutral
    days make no claim. Returns None when there is not enough history for
    a meaningful sample.
    """
    n = len(candles)
    if n < WARMUP_DAYS + FORWARD_DAYS + MIN_SAMPLES:
        return None

    hits = 0
    samples = 0
    bullish_returns: list[float] = []
    bearish_returns: list[float] = []
    for t in range(WARMUP_DAYS, n - FORWARD_DAYS):
        window = candles[: t + 1]
        direction = analyze_fn(window, len(window))["outlook"]["direction"]
        if direction not in ("bullish", "bearish"):
            continue
        close_now = float(candles[t][4])
        close_fwd = float(candles[t + FORWARD_DAYS][4])
        if close_now <= 0:
            continue
        fwd_return = close_fwd / close_now - 1
        samples += 1
        if (direction == "bullish") == (fwd_return > 0):
            hits += 1
        (bullish_returns if direction == "bullish" else bearish_returns).append(fwd_return)

    if samples < MIN_SAMPLES:
        return None

    def _pct(values: list[float]) -> str | None:
        if not values:
            return None
        return f"{sum(values) / len(values) * 100:.2f}"

    return {
        "hit_rate_pct": f"{hits / samples * 100:.1f}",
        "samples": samples,
        "forward_days": FORWARD_DAYS,
        "avg_bullish_return_pct": _pct(bullish_returns),
        "avg_bearish_return_pct": _pct(bearish_returns),
        "from": datetime.fromtimestamp(candles[WARMUP_DAYS][0] / 1000, tz=timezone.utc).date().isoformat(),
        "to": datetime.fromtimestamp(candles[n - FORWARD_DAYS - 1][0] / 1000, tz=timezone.utc).date().isoformat(),
    }
