"""Standalone verification of the commodity asset class.

Run: .venv\\Scripts\\python test_commodities.py
"""
from app.services.instruments import INSTRUMENTS, INSTRUMENTS_BY_MARKET
from app.services.twelvedata import TwelveDataService

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


EXPECTED = {
    "XAU-EUR": ("XAU/EUR", "Gold"),
    "XAG-EUR": ("XAG/EUR", "Silver"),
    "XPT-EUR": ("XPT/USD", "Platinum"),
    "XPD-EUR": ("XPD/USD", "Palladium"),
    "WTI-EUR": ("WTI/USD", "WTI Crude Oil"),
    "XBR-EUR": ("XBR/USD", "Brent Crude Oil"),
    "URALS-EUR": ("URALS/USD", "Urals Crude Oil"),
}

print("Instrument definitions")
commodities = [i for i in INSTRUMENTS if i.asset_class == "commodity"]
check("7 commodity instruments", len(commodities) == 7, f"got {len(commodities)}")
for market, (td_symbol, name) in EXPECTED.items():
    inst = INSTRUMENTS_BY_MARKET.get(market)
    check(f"{market} exists", inst is not None)
    if inst is None:
        continue
    check(f"{market} td_symbol", inst.td_symbol == td_symbol,
          f"got {inst.td_symbol!r}")
    check(f"{market} name", inst.name == name, f"got {inst.name!r}")
    check(f"{market} quote currency", inst.currency == td_symbol.split("/")[1],
          f"got {inst.currency!r}")

print("Stock/fund symbols unchanged")
aapl = INSTRUMENTS_BY_MARKET["AAPL-EUR"]
spy = INSTRUMENTS_BY_MARKET["SPY-EUR"]
check("AAPL td_symbol is ticker style", aapl.td_symbol == "AAPL")
check("SPY td_symbol is ticker style", spy.td_symbol == "SPY")

print("Service market map")
svc = TwelveDataService()
svc._load_markets()
for market, (_, name) in EXPECTED.items():
    info = svc.markets.get(market)
    check(f"{market} loaded", info is not None)
    if info is None:
        continue
    check(f"{market} asset_class", info["asset_class"] == "commodity",
          f"got {info['asset_class']!r}")
    check(f"{market} seeded name", info.get("name") == name,
          f"got {info.get('name')!r}")
    check(f"{market} quote is EUR", info["quote"] == "EUR")
check("stock market has no seeded name",
      svc.markets["AAPL-EUR"].get("name") is None)

print("Press-release markets exclude commodities")
pr_markets = [
    m for m, i in svc._instruments.items() if i.asset_class != "commodity"
]
check("no commodity in press-release universe",
      not any(m in EXPECTED for m in pr_markets))

print()
print(f"{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
