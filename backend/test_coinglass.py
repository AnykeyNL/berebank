"""Unit tests for Coinglass integration helpers."""

import asyncio

from app.services import coinglass
from app.services import fable5_analysis

passed = 0
failed = 0


def check(name: str, condition: bool) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK  {name}")
    else:
        failed += 1
        print(f" FAIL {name}")


check("resolve direct symbol", coinglass.resolve_coinglass_symbol("BTC", {"BTC": {}}) == "BTC")
check(
    "resolve 1000-prefix alias",
    coinglass.resolve_coinglass_symbol("PEPE", {"1000PEPE": {}}) == "1000PEPE",
)
check("resolve missing symbol", coinglass.resolve_coinglass_symbol("ZZZ", {"BTC": {}}) is None)

avg = coinglass._avg_funding(
    {
        "stablecoin_margin_list": [{"funding_rate": 0.01}, {"funding_rate": 0.03}],
        "token_margin_list": [],
    }
)
check("average funding", avg == 0.02)

ctx = {
    "context_type": "crypto",
    "funding_rate_avg": 0.06,
    "open_interest_change_percent_24h": 8.0,
}
funding = fable5_analysis._funding_regime(ctx)
oi = fable5_analysis._oi_momentum(ctx, [0, 3_600_000], [100.0, 100.0])
check("funding crowded longs", funding["signal"] == "bearish")
check("oi rising", oi["signal"] == "bullish")

async def _fetch_map():
    coinglass.coinglass_service.set_api_key("test-key")
    import time
    coinglass._funding_cache = (
        time.monotonic(),
        {"BTC": {"funding_rate_avg": 0.01, "exchange_count": 3}},
    )
    payload = await coinglass.get_symbol_derivatives("BTC")
    check("cached funding merged", payload.get("funding_rate_avg") == 0.01)


asyncio.run(_fetch_map())

print(f"\n{passed} passed, {failed} failed")
if failed:
    raise SystemExit(1)
