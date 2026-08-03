"""Scratch: exercise the Opus MCP tools against the smoke database."""
import asyncio
import json
import os

os.environ.setdefault(
    "BEREBANK_DATABASE_URL",
    "sqlite:///C:\\projects\\berebank\\backend\\berebank_opus_smoke.db",
)

from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.services import opus_store  # noqa: E402
from app.services.instruments import INSTRUMENTS_BY_MARKET  # noqa: E402
from app.services.market_data import market_data_service  # noqa: E402


def fake_markets():
    from sqlalchemy import distinct, select

    from app.models import MarketCandle

    db = SessionLocal()
    try:
        markets = [m for (m,) in db.execute(select(distinct(MarketCandle.market)))]
    finally:
        db.close()
    out = {}
    for market in markets:
        instrument = INSTRUMENTS_BY_MARKET.get(market)
        out[market] = {
            "market": market,
            "base": market.split("-")[0],
            "quote": "EUR",
            "name": market,
            "asset_class": instrument.asset_class if instrument else "crypto",
        }
    return out


MARKETS = fake_markets()
type(market_data_service).markets = property(lambda self: MARKETS)
market_data_service.get_market = lambda market: MARKETS.get(market)
market_data_service.get_price = lambda market: {"last": None, "market_open": True}
opus_store._asset_classes = lambda: {m: i["asset_class"] for m, i in MARKETS.items()}

import app.mcp_server as mcp_server  # noqa: E402


async def main():
    db = SessionLocal()
    user = db.query(User).filter(User.email == "manager@berebank.nl").one()
    mcp_server._current_user = lambda _db: user

    tools = await mcp_server.mcp.list_tools()
    opus_tools = [t.name for t in tools if "opus" in t.name]
    print("registered opus tools:", opus_tools)
    for tool in tools:
        if "opus" in tool.name:
            print(f"\n--- {tool.name} schema args:", sorted(tool.inputSchema["properties"]))
            print((tool.description or "")[:200].replace("\n", " "), "...")

    rankings = await mcp_server.get_opus_rankings(horizon="1w", limit=5)
    print("\nrankings keys:", sorted(rankings))
    print("basket:", rankings["basket"])
    for row in rankings["rankings"]:
        print(f"  #{row['buy_rank']} {row['market']} {row['action']} "
              f"net={row['net_edge_pct']} ord={row['suggested_order_type']}")

    sells = await mcp_server.get_opus_rankings(horizon="4w", side="sell", limit=3,
                                               asset_class="crypto")
    print("\nsell side:", [(r["market"], r["sell_rank"], r["action"]) for r in sells["rankings"]])

    for bad in (
        dict(horizon="bogus"),
        dict(side="hodl"),
        dict(asset_class="bonds"),
        dict(limit=0),
    ):
        try:
            await mcp_server.get_opus_rankings(**bad)
            print("no error for", bad)
        except Exception as exc:
            print("rejected", bad, "->", exc)

    analysis = await mcp_server.get_opus_analysis("BTC-EUR", range="90d", horizon="1w")
    print("\nanalysis keys:", sorted(analysis))
    print("recommendation:", json.dumps(analysis["recommendation"])[:400])

    try:
        await mcp_server.get_opus_analysis("BTC-EUR", horizon="2y")
    except Exception as exc:
        print("rejected horizon ->", exc)

    advice = await mcp_server.get_opus_portfolio_advice(horizon="1w")
    print("\nadvice keys:", sorted(advice))
    print("cash:", advice["cash_eur"], "fee:", advice["fee_tier"])
    print("holdings:", json.dumps(advice["holdings"])[:600])
    print("candidates:", [c["market"] for c in advice["buy_candidates"]])
    print("allocation:", json.dumps(advice["suggested_allocation"], indent=1)[:800])
    db.close()


asyncio.run(main())
