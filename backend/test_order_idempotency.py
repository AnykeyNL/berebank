"""Standalone verification of client_order_id replay and validate_only previews.

Run: .venv\\Scripts\\python test_order_idempotency.py
"""
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Holding, Order, User
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


def setup_account(db, email: str) -> Account:
    user = User(email=email, password_hash="x", display_name="T")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, balance_eur=Decimal("1000"))
    db.add(account)
    db.flush()
    db.add(Holding(account_id=account.id, asset="BTC", amount=Decimal("1")))
    db.commit()
    return account


def order_count(db, account_id) -> int:
    return len(db.scalars(select(Order).where(Order.account_id == account_id)).all())


with patch("app.services.trading.market_data_service") as mds:
    mds.get_market.return_value = MARKET
    mds.get_price.return_value = dict(PRICE)

    db = TestSession()
    account = setup_account(db, "t@t.t")
    other = setup_account(db, "other@t.t")

    print("client_order_id: first placement")
    first = trading.place_order(
        db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
        client_order_id="run-1",
    )
    check("order created", isinstance(first, Order) and first.status == "filled")
    check("id stored on the order", first.client_order_id == "run-1")
    check("one order on the account", order_count(db, account.id) == 1)
    balance_after_first = db.get(Account, account.id).balance_eur

    print("client_order_id: replaying the same call")
    replay = trading.place_order(
        db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
        client_order_id="run-1",
    )
    check("same order returned", replay.id == first.id)
    check("no second order placed", order_count(db, account.id) == 1)
    check(
        "balance untouched by the replay",
        db.get(Account, account.id).balance_eur == balance_after_first,
    )

    print("client_order_id: a replay ignores differing parameters")
    # A retry after a timeout may be rebuilt from a fresh plan; the stored
    # order wins so the account can never be charged twice.
    odd_replay = trading.place_order(
        db, account, "BTC-EUR", "sell", "limit", Decimal("0.5"), None, Decimal("200"),
        client_order_id="run-1",
    )
    check("still the original order", odd_replay.id == first.id)
    check("still one order", order_count(db, account.id) == 1)

    print("client_order_id: a different id places a new order")
    second = trading.place_order(
        db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
        client_order_id="run-2",
    )
    check("new order created", second.id != first.id)
    check("two orders now", order_count(db, account.id) == 2)

    print("client_order_id: ids are scoped to one account")
    theirs = trading.place_order(
        db, other, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
        client_order_id="run-1",
    )
    check("other account gets its own order", theirs.id != first.id)
    check("other account has one order", order_count(db, other.id) == 1)

    print("client_order_id: omitting it never dedupes")
    trading.place_order(db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None)
    trading.place_order(db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None)
    check("both anonymous orders stored", order_count(db, account.id) == 4)

    print("client_order_id: validation")
    expect_error(
        "blank id rejected",
        lambda: trading.place_order(
            db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
            client_order_id="   ",
        ),
        "cannot be empty",
    )
    expect_error(
        "over-long id rejected",
        lambda: trading.place_order(
            db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None,
            client_order_id="x" * 65,
        ),
        "at most 64",
    )
    check(
        "surrounding whitespace is normalised away",
        trading.normalize_client_order_id("  run-1  ") == "run-1",
    )

    print("client_order_id: lookup helper")
    check(
        "finds the stored order",
        trading.find_client_order(db, account, "run-1").id == first.id,
    )
    check("unknown id returns None", trading.find_client_order(db, account, "nope") is None)

    print("validate_only: market buy preview")
    before = db.get(Account, account.id).balance_eur
    orders_before = order_count(db, account.id)
    preview = trading.preview_order(
        db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None
    )
    check("marked as a preview", preview["valid"] and preview["validated_only"])
    check("nothing was stored", order_count(db, account.id) == orders_before)
    check("balance untouched", db.get(Account, account.id).balance_eur == before)
    check("priced at the ask", preview["price"] == "101" and preview["price_basis"] == "ask")
    check("eur value echoed", preview["eur_value"] == "100")
    check("taker fee quoted", preview["fee_type"] == "taker" and preview["fee_eur"] == "0.25")
    check("fee rate in percent", preview["fee_rate_pct"] == "0.25")
    check(
        "balance after includes the fee",
        Decimal(preview["balance_after_eur"]) == before - Decimal("100") - Decimal("0.25"),
    )
    check("market orders fill immediately", preview["fills_immediately"] is True)
    check("minimum order reported", preview["minimum_order_eur"] == "5")

    print("validate_only: amount precision is echoed back")
    precise = trading.preview_order(
        db, account, "BTC-EUR", "sell", "limit", Decimal("0.1234"), None, Decimal("100")
    )
    check("four decimals accepted", precise["amount"] == "0.1234")
    check("locked amount reported", precise["locked_amount"] == "0.1234")
    check("maker fee for limit orders", precise["fee_type"] == "maker")
    check("sell limit does not move cash", precise["balance_after_eur"] == precise["balance_eur"])
    check("fee only on fill", precise["fee_charged_at"] == "fill")

    print("validate_only: limit buy reserves rather than spends")
    limit_buy = trading.preview_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("90")
    )
    reserved = Decimal(limit_buy["reserved_eur"])
    check(
        "reservation includes the maker fee",
        reserved == Decimal("90") + Decimal("0.135"),
        f"(got {reserved})",
    )
    check(
        "balance after equals balance minus reservation",
        Decimal(limit_buy["balance_after_eur"])
        == Decimal(limit_buy["balance_eur"]) - reserved,
    )
    check("fee charged at placement", limit_buy["fee_charged_at"] == "placement")
    check("resting order does not fill", limit_buy["fills_immediately"] is False)

    print("validate_only: a crossing limit order says so")
    crossing = trading.preview_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("150")
    )
    check("crossing limit flagged", crossing["fills_immediately"] is True)

    print("validate_only: stop-loss preview")
    stop = trading.preview_order(
        db, account, "BTC-EUR", "sell", "stop_loss", Decimal("0.5"), None, None, Decimal("90")
    )
    check("priced at the trigger", stop["price_basis"] == "trigger_price" and stop["price"] == "90")
    check("asset gets locked", stop["locked_amount"] == "0.5")
    check("taker fee estimated", stop["fee_type"] == "taker")

    print("validate_only: rejections match a real placement")
    expect_error(
        "below minimum rejected",
        lambda: trading.preview_order(
            db, account, "BTC-EUR", "buy", "market", None, Decimal("1"), None
        ),
        "minimum order",
    )
    expect_error(
        "unaffordable order rejected",
        lambda: trading.preview_order(
            db, account, "BTC-EUR", "buy", "market", None, Decimal("100000"), None
        ),
        "insufficient eur",
    )
    expect_error(
        "overselling rejected",
        lambda: trading.preview_order(
            db, account, "BTC-EUR", "sell", "limit", Decimal("99"), None, Decimal("100")
        ),
        "insufficient btc",
    )
    check(
        "a rejected preview leaves the holding intact",
        trading._get_holding(db, account.id, "BTC").amount > 0,
    )

    print("validate_only: closed exchanges still reject market orders")
    mds.get_price.return_value = {**PRICE, "market_open": False}
    expect_error(
        "closed market rejected",
        lambda: trading.preview_order(
            db, account, "BTC-EUR", "buy", "market", None, Decimal("100"), None
        ),
        "currently closed",
    )
    mds.get_price.return_value = dict(PRICE)

    db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
