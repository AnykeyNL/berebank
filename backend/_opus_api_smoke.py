"""Scratch: exercise the Opus REST endpoints against the smoke database."""
import json
import os

os.environ.setdefault(
    "BEREBANK_DATABASE_URL",
    "sqlite:///C:\\projects\\berebank\\backend\\berebank_opus_smoke.db",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
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

with TestClient(app) as client:
    login = client.post(
        "/auth/login",
        json={"email": "manager@berebank.nl", "password": "manager123"},
    )
    print("login:", login.status_code)
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for horizon in ("1d", "1w", "4w"):
        resp = client.get(
            "/markets/opus-rankings",
            params={"horizon": horizon, "limit": 5},
            headers=headers,
        )
        print(f"\nrankings {horizon}: {resp.status_code}")
        if resp.status_code != 200:
            print(resp.text[:2000])
            continue
        body = resp.json()
        print("keys:", sorted(body))
        print("basket:", body["basket"])
        for row in body["rankings"]:
            print(
                f"  #{row['buy_rank']} {row['market']} {row['action']} "
                f"score={row['score']} buy={row['buy_score']} net={row['net_edge_pct']} "
                f"ord={row['suggested_order_type']} conf={row['confidence']}"
            )

    resp = client.get("/markets/opus-rankings", params={"horizon": "bogus"}, headers=headers)
    print("\nbad horizon:", resp.status_code, resp.json().get("detail"))

    resp = client.get("/markets/opus-outlooks", params={"horizon": "1w"}, headers=headers)
    print("outlooks:", resp.status_code, len(resp.json().get("outlooks", {})))
    sample = next(iter(resp.json()["outlooks"].items()))
    print("outlook sample:", sample)

    resp = client.get(
        "/markets/BTC-EUR/opus-analysis",
        params={"range": "90d", "horizon": "1w"},
        headers=headers,
    )
    print("\nanalysis:", resp.status_code)
    if resp.status_code == 200:
        body = resp.json()
        print("keys:", sorted(body))
        print("outlook:", body["outlook"]["direction"], body["outlook"]["score"],
              body["outlook"]["confidence"], "mode:", body["mode"])
        print("recommendation:", json.dumps(body["recommendation"], indent=None)[:600])
        print("cross_section:", body["cross_section"])
        print("gates:", body["gates"])
        print("calibration:", json.dumps(body["calibration"])[:500])
        print("track_record:", body["track_record"])
        print("live:", body["live_track_record"], body["live_track_record_all"])
        print("strategies:", len(body["strategies"]), "candles:", len(body["candles"]))
        print("contrib top:", body["outlook"]["contributions"][:4])
        print("one feature:", json.dumps(body["strategies"]["mom_21"])[:400])
    else:
        print(resp.text[:3000])
