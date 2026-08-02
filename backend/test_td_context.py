"""Standalone verification of Twelve Data supplementary context helpers.

Run: .venv\\Scripts\\python test_td_context.py
"""
from datetime import date, timedelta

from app.services import td_context

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


print("macro_regime")
check("risk_off on high VIX", td_context._macro_regime(26.0, 0.5) == "risk_off")
check("risk_off on inverted curve", td_context._macro_regime(18.0, -0.1) == "risk_off")
check("risk_on on calm steep curve", td_context._macro_regime(15.0, 0.6) == "risk_on")
check("neutral otherwise", td_context._macro_regime(20.0, 0.2) == "neutral")
check("neutral with only us2y path later", td_context._macro_regime(None, None) == "neutral")

print("macro_has_data")
check(
    "detects us2y only",
    td_context._macro_has_data({"us2y_yield": 4.2, "vix_level": None, "us10y_yield": None}),
)
check(
    "empty payload",
    not td_context._macro_has_data(
        {"us2y_yield": None, "vix_level": None, "us10y_yield": None}
    ),
)

print("vix_proxy")
vixy_rows = [(i * 86400000, 20.0 + (i % 5)) for i in range(80)]
proxy = td_context._vix_proxy_level(vixy_rows)
check("proxy level in range", proxy is not None and 10.0 <= proxy <= 40.0)
by_day = td_context._vix_proxy_by_day(vixy_rows)
check("proxy by day populated", len(by_day) == len(vixy_rows))

print("insider_signal")
today = date.today()
rows = [
    {"date": today.isoformat(), "action": "Buy"},
    {"date": today.isoformat(), "action": "Buy"},
    {"date": today.isoformat(), "action": "Buy"},
    {"date": (today - timedelta(days=10)).isoformat(), "action": "Sell"},
]
signal, buys, sells = td_context._insider_signal(rows)
check("net buying", signal == "bullish" and buys == 3 and sells == 1)

print("macro_features_at")
ctx = {
    "vix_by_day": {"2024-01-01": 22.0},
    "yield_spread_by_day": {"2024-01-01": 0.4},
    "vix_level": 22.0,
    "yield_spread": 0.4,
    "earnings_near": True,
    "insider_signal": "bearish",
}
ts = int(__import__("calendar").timegm((2024, 1, 1, 0, 0, 0)) * 1000)
hist = td_context.macro_features_at(ctx, ts, current_only=False)
check("historical VIX lookup", hist["vix_normalized"] is not None)
partial = td_context.macro_features_at(
    {"us2y_yield": 4.3, "vix_level": None, "yield_spread": None},
    ts,
    current_only=True,
)
check("partial macro still serializable fields", partial["yield_spread"] is None)

print("serialize_context partial")
partial_ctx = {
    "macro_regime": "neutral",
    "us2y_yield": 4.291,
    "vix_level": None,
    "yield_spread": None,
}
serialized = td_context.serialize_context(partial_ctx)
check("partial context not null", serialized is not None)
check("partial us2y present", serialized["us2y_yield"] == "4.291")

current = td_context.macro_features_at(ctx, ts, current_only=True)
check("current earnings flag", current["earnings_proximity"] == 1.0)
check("current insider bearish", current["insider_activity"] == -1.0)

print("serialize_context")
serialized = td_context.serialize_context(ctx)
check("serializes spread", serialized["yield_spread"] == "0.4")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
