"""Standalone verification of the stop-loss order flow (in-memory SQLite).

Run: .venv\\Scripts\\python test_stop_loss.py
"""
import asyncio
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Holding, Order, Trade, User
from app.services import trading
from app.services.trading import TradingError

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

PRICE = {"last": Decimal("100"), "bid": Decimal("99"), "ask": Decimal("101"), "market_open": None}
MARKET = {"base": "BTC", "quote": "EUR", "market": "BTC-EUR"}

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def expect_error(name: str, fn, needle: str):
    try:
        fn()
        check(name, False, "(no error raised)")
    except TradingError as exc:
        check(name, needle.lower() in exc.message.lower(), f"(got: {exc.message})")


def setup_account(db) -> Account:
    user = User(email="t@t.t", password_hash="x", display_name="T")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, balance_eur=Decimal("1000"))
    db.add(account)
    db.flush()
    db.add(Holding(account_id=account.id, asset="BTC", amount=Decimal("1")))
    db.commit()
    return account


def holding_amount(db, account_id) -> Decimal:
    h = trading._get_holding(db, account_id, "BTC")
    return h.amount if h else Decimal("0")


with patch("app.services.trading.market_data_service") as mds:
    mds.get_market.return_value = MARKET
    mds.get_price.return_value = dict(PRICE)

    db = TestSession()
    account = setup_account(db)

    print("Validation:")
    expect_error(
        "buy-side stop-loss rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "buy", "stop_loss",
                                    Decimal("0.1"), None, None, Decimal("90")),
        "only be sell",
    )
    expect_error(
        "missing trigger_price rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    Decimal("0.1"), None, None, None),
        "require both amount and trigger_price",
    )
    expect_error(
        "trigger above current price rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    Decimal("0.1"), None, None, Decimal("120")),
        "below the current price",
    )
    expect_error(
        "below minimum order rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    Decimal("0.01"), None, None, Decimal("90")),
        "minimum order",
    )
    expect_error(
        "amount_quote rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    None, Decimal("50"), None, Decimal("90")),
        "amount_quote",
    )
    expect_error(
        "insufficient holding rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    Decimal("2"), None, None, Decimal("90")),
        "insufficient btc",
    )

    print("Placement:")
    order = trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                Decimal("0.5"), None, None, Decimal("90"))
    check("order is open", order.status == "open")
    check("order type stored", order.order_type == "stop_loss")
    check("trigger price stored", order.trigger_price == Decimal("90"))
    check("asset reserved (holding debited)", holding_amount(db, account.id) == Decimal("0.5"))
    check("market registered for matching", "BTC-EUR" in trading._open_limit_markets)

    print("No fill above trigger:")
    asyncio.run(trading.match_limit_orders(
        [{"market": "BTC-EUR", "last": Decimal("95"), "bid": Decimal("94"), "ask": Decimal("96")}],
        TestSession,
    ))
    db.expire_all()
    check("order still open at bid 94", db.get(Order, order.id).status == "open")

    print("No fill while exchange closed:")
    asyncio.run(trading.match_limit_orders(
        [{"market": "BTC-EUR", "last": Decimal("85"), "bid": Decimal("84"),
          "ask": Decimal("86"), "market_open": False}],
        TestSession,
    ))
    db.expire_all()
    check("order still open when market closed", db.get(Order, order.id).status == "open")

    print("Trigger fill:")
    balance_before = db.get(Account, account.id).balance_eur
    asyncio.run(trading.match_limit_orders(
        [{"market": "BTC-EUR", "last": Decimal("89"), "bid": Decimal("88"), "ask": Decimal("90")}],
        TestSession,
    ))
    db.expire_all()
    filled = db.get(Order, order.id)
    check("order filled on bid <= trigger", filled.status == "filled")
    check("filled at live bid (88)", filled.filled_price == Decimal("88"))
    trade = db.query(Trade).filter_by(order_id=order.id).one()
    eur_value = Decimal("0.5") * Decimal("88")
    taker_fee = (eur_value * Decimal("0.0025")).quantize(Decimal("0.01"))
    check("taker fee charged", filled.fee_paid == taker_fee, f"(fee: {filled.fee_paid})")
    check(
        "balance credited with proceeds minus fee",
        db.get(Account, account.id).balance_eur == balance_before + eur_value - taker_fee,
    )
    check("trade recorded as sell", trade.side == "sell" and trade.amount == Decimal("0.5"))
    check("market deregistered after fill", "BTC-EUR" not in trading._open_limit_markets)

    print("Cancel:")
    order2 = trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                 Decimal("0.2"), None, None, Decimal("80"))
    check("second order reserves asset", holding_amount(db, account.id) == Decimal("0.3"))
    trading.cancel_order(db, account, order2.id)
    db.expire_all()
    check("cancelled status", db.get(Order, order2.id).status == "cancelled")
    check("asset returned on cancel", holding_amount(db, account.id) == Decimal("0.5"))
    check("market deregistered after cancel", "BTC-EUR" not in trading._open_limit_markets)

    print("Immediate-trigger guard:")
    mds.get_price.return_value = {"last": Decimal("100"), "bid": None, "ask": None, "market_open": None}
    expect_error(
        "trigger >= last (no bid) rejected",
        lambda: trading.place_order(db, account, "BTC-EUR", "sell", "stop_loss",
                                    Decimal("0.1"), None, None, Decimal("100")),
        "below the current price",
    )

    db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
