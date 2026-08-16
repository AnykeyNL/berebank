"""Standalone verification of the analysis response trimmer.

Run: .venv\\Scripts\\python test_payload_trim.py
"""
from app.services import analysis
from app.services.payload import compact_analysis, shape_analysis

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
    return [
        [start_ts + i * 3_600_000, str(c), str(c * 1.01), str(c * 0.99), str(c), "100.0"]
        for i, c in enumerate(closes)
    ]


closes = [100.0 + (i % 17) - (i % 5) * 0.5 for i in range(200)]
full = {
    "market": "BTC-EUR",
    "range": "30d",
    **analysis.analyze(make_candles(closes), display_count=120),
}

print("full response has the chart payload")
check("candles present", bool(full["candles"]))
check(
    "at least one strategy carries a series",
    any(s.get("series") for s in full["strategies"].values()),
)

print("compact response drops the chart payload")
compact = compact_analysis(full)
check("candles removed", "candles" not in compact)
check(
    "every series emptied",
    all(s.get("series") == {} for s in compact["strategies"].values()),
    f"(got {[n for n, s in compact['strategies'].items() if s.get('series')]})",
)

print("compact response keeps the verdict")
check("market kept", compact["market"] == "BTC-EUR")
check("range kept", compact["range"] == "30d")
check("generated_at kept", compact["generated_at"] == full["generated_at"])
check(
    "same strategies",
    set(compact["strategies"]) == set(full["strategies"]),
)
for name, strategy in compact["strategies"].items():
    original = full["strategies"][name]
    check(
        f"{name}: signal, reason, values and explanation kept",
        strategy["signal"] == original["signal"]
        and strategy["reason"] == original["reason"]
        and strategy["values"] == original["values"]
        and strategy.get("explanation") == original.get("explanation"),
    )

print("trimming never touches the cached original")
check("original still has candles", bool(full["candles"]))
check(
    "original series intact",
    any(s.get("series") for s in full["strategies"].values()),
)

print("outlook and track record survive the trim")
kimi_like = {
    "market": "BTC-EUR",
    "candles": [[1, "1", "1", "1", "1", "1"]],
    "outlook": {"direction": "bullish", "score": "42", "confidence": "medium"},
    "track_record": {"hit_rate_pct": "55"},
    "strategies": {"trend": {"signal": "bullish", "series": {"sma20": [[1, "1"]]}}},
    "context": {"vix": "14"},
}
trimmed = compact_analysis(kimi_like)
check("outlook kept", trimmed["outlook"] == kimi_like["outlook"])
check("track_record kept", trimmed["track_record"] == kimi_like["track_record"])
check("context kept", trimmed["context"] == kimi_like["context"])
check("candles gone", "candles" not in trimmed)
check("series emptied", trimmed["strategies"]["trend"]["series"] == {})
check("signal kept", trimmed["strategies"]["trend"]["signal"] == "bullish")
check("source dict untouched", kimi_like["strategies"]["trend"]["series"] != {})

print("shape_analysis honours the flag")
check("verbose=True returns the original object", shape_analysis(full, True) is full)
check("verbose=False trims", "candles" not in shape_analysis(full, False))

print("strategies without a series are passed through untouched")
opus_like = {"strategies": {"momentum": {"signal": "bearish", "series": {}}}}
check(
    "empty series left alone",
    compact_analysis(opus_like)["strategies"]["momentum"]
    is opus_like["strategies"]["momentum"],
)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
