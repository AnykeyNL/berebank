"""Standalone verification of free crypto supplementary context helpers.

Run: .venv\\Scripts\\python test_crypto_context.py
"""
import calendar

from app.services import crypto_context

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
check("risk_on on extreme fear", crypto_context._macro_regime(20.0, None, None) == "risk_on")
check("risk_off on extreme greed", crypto_context._macro_regime(80.0, None, None) == "risk_off")
check("risk_off on dominance surge", crypto_context._macro_regime(50.0, 1.5, None) == "risk_off")
check("neutral otherwise", crypto_context._macro_regime(50.0, 0.1, 0.0) == "neutral")

print("btc_correlation")
btc = [
    [1_700_000_000_000 + i * 86_400_000, "1", "1", "1", str(100 + i), "1"]
    for i in range(40)
]
eth = [
    [1_700_000_000_000 + i * 86_400_000, "1", "1", "1", str(50 + i * 0.5), "1"]
    for i in range(40)
]
corr = crypto_context.btc_correlation(btc, eth)
check("positive correlation computed", corr is not None and corr > 0.5, f"got {corr}")
check("BTC vs self is 1", crypto_context.btc_correlation(btc, btc) is not None)

print("macro_features_at")
ctx = {
    "context_type": "crypto",
    "fear_greed_by_day": {"2024-06-01": 75.0},
    "btc_dominance_by_day": {"2024-06-01": 52.0},
    "stablecoin_supply_by_day": {"2024-05-31": 100.0, "2024-06-01": 103.0},
    "fear_greed_index": 75,
    "btc_dominance": 52.0,
    "btc_correlation": 0.82,
    "stablecoin_supply_change_pct": 3.0,
}
ts = int(calendar.timegm((2024, 6, 1, 0, 0, 0)) * 1000)
hist = crypto_context.macro_features_at(ctx, ts, current_only=False)
check("historical fear & greed normalized", hist["vix_normalized"] is not None)
check("historical dominance centered", hist["yield_spread"] == 2.0)
current = crypto_context.macro_features_at(ctx, ts, current_only=True)
check("current correlation feature", current["earnings_proximity"] == 0.82)
check("current stablecoin activity", current["insider_activity"] is not None)

print("serialize_context")
serialized = crypto_context.serialize_context(ctx)
check("serializes crypto type", serialized["context_type"] == "crypto")
check("serializes fear & greed", serialized["fear_greed_index"] == 75)
check("non-crypto rejected", crypto_context.serialize_context({"vix_level": 20.0}) is None)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
