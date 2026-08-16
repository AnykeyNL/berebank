"""Standalone verification of order expiry (in-memory SQLite).

Run: .venv\\Scripts\\python test_order_expiry.py
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Holding, Order, User
from app.services import trading
from app.services.trading import TradingError

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

PRICE = {"last": Decimal("100"), "bid": Decimal("99"), "ask": Decimal("101"), "market_open": None}
CRYPTO = {"base": "BTC", "quote": "EUR", "market": "BTC-EUR", "asset_class": "crypto"}
STOCK = {"base": "AAPL", "quote": "EUR", "market": "AAPL-EUR", "asset_class": "stock"}

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


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def setup_account(db) -> Account:
    user = User(email="t@t.t", password_hash="x", display_name="T")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, balance_eur=Decimal("10000"))
    db.add(account)
    db.flush()
    db.add(Holding(account_id=account.id, asset="BTC", amount=Decimal("10")))
    db.commit()
    return account


def holding_amount(db, account_id, asset="BTC") -> Decimal:
    holding = trading._get_holding(db, account_id, asset)
    return holding.amount if holding else Decimal("0")


SAT_EVENING = utc("2026-08-15T20:00")

print("resolve_expiry: defaults")
tif, expires, sessions = trading.resolve_expiry("crypto", "limit", None, None, None)
check("no expiry means gtc", (tif, expires, sessions) == ("gtc", None, None))
tif, expires, sessions = trading.resolve_expiry("crypto", "limit", "gtc", None, None)
check("explicit gtc has no expiry", expires is None and tif == "gtc")

print("resolve_expiry: sessions, not wall-clock hours")
tif, expires, sessions = trading.resolve_expiry(
    "stock", "limit", None, None, 2, now=SAT_EVENING
)
check("passing sessions implies gtd", tif == "gtd")
check("sessions recorded", sessions == 2)
check(
    "Saturday + 2 NYSE sessions is Tuesday's close",
    expires == utc("2026-08-18T20:00"),
    f"(got {expires})",
)
check(
    "that is well past the 40 wall-clock hours a naive reading would give",
    expires - SAT_EVENING > timedelta(hours=40),
)
_, crypto_expires, _ = trading.resolve_expiry(
    "crypto", "limit", None, None, 2, now=SAT_EVENING
)
check(
    "a crypto session is a 24-hour day",
    crypto_expires == SAT_EVENING + timedelta(days=2),
)

print("resolve_expiry: day orders")
tif, expires, sessions = trading.resolve_expiry("stock", "limit", "day", None, None, now=SAT_EVENING)
check("day resolves to the next close", expires == utc("2026-08-17T20:00"), f"(got {expires})")
check("day records one session", tif == "day" and sessions == 1)

print("resolve_expiry: explicit timestamps")
target = utc("2026-08-20T16:00")
tif, expires, sessions = trading.resolve_expiry(
    "stock", "limit", "gtd", target, None, now=SAT_EVENING
)
check("expires_at kept as given", expires == target and sessions is None)
naive = datetime(2026, 8, 20, 16, 0)
_, expires, _ = trading.resolve_expiry("stock", "limit", None, naive, None, now=SAT_EVENING)
check("naive timestamps are read as UTC", expires == target)

print("resolve_expiry: contradictions are rejected")
expect_error(
    "gtc with an expiry rejected",
    lambda: trading.resolve_expiry("stock", "limit", "gtc", target, None, now=SAT_EVENING),
    "never expires",
)
expect_error(
    "day with an expiry rejected",
    lambda: trading.resolve_expiry("stock", "limit", "day", target, None, now=SAT_EVENING),
    "already expires",
)
expect_error(
    "gtd without an expiry rejected",
    lambda: trading.resolve_expiry("stock", "limit", "gtd", None, None, now=SAT_EVENING),
    "requires expires_at or expires_in_sessions",
)
expect_error(
    "both forms at once rejected",
    lambda: trading.resolve_expiry("stock", "limit", "gtd", target, 2, now=SAT_EVENING),
    "not both",
)
expect_error(
    "a past expiry rejected",
    lambda: trading.resolve_expiry(
        "stock", "limit", "gtd", utc("2026-08-01T16:00"), None, now=SAT_EVENING
    ),
    "must be in the future",
)
expect_error(
    "an unknown time_in_force rejected",
    lambda: trading.resolve_expiry("stock", "limit", "fok", None, None, now=SAT_EVENING),
    "time_in_force must be one of",
)
expect_error(
    "too many sessions rejected",
    lambda: trading.resolve_expiry("stock", "limit", None, None, 9999, now=SAT_EVENING),
    "between 1 and",
)
expect_error(
    "expiry on a market order rejected",
    lambda: trading.resolve_expiry("crypto", "market", "day", None, None, now=SAT_EVENING),
    "resting orders only",
)
check(
    "a plain market order is unaffected",
    trading.resolve_expiry("crypto", "market", None, None, None) == ("gtc", None, None),
)

with patch("app.services.trading.market_data_service") as mds:
    mds.get_market.return_value = CRYPTO
    mds.get_price.return_value = dict(PRICE)

    db = TestSession()
    account = setup_account(db)

    print("Placement stores the resolved expiry")
    order = trading.place_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        expires_in_sessions=2,
    )
    check("time_in_force stored", order.time_in_force == "gtd")
    check("intent stored", order.expires_after_sessions == 2)
    check("resolved moment stored", order.expires_at is not None)
    check(
        "roughly two days out for crypto",
        timedelta(days=1, hours=23) < order.expires_at - datetime.now(timezone.utc) < timedelta(days=2, hours=1),
        f"(got {order.expires_at})",
    )
    check("default orders stay gtc", trading.place_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
    ).expires_at is None)

    print("The preview reports the expiry it would agree to")
    preview = trading.preview_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        expires_in_sessions=3,
    )
    check("preview carries time_in_force", preview["time_in_force"] == "gtd")
    check("preview carries sessions", preview["expires_after_sessions"] == 3)
    check("preview carries a Z-suffixed moment", preview["expires_at"].endswith("Z"))

    print("A due order expires and gives back its reservation")
    balance_before = db.get(Account, account.id).balance_eur
    due = trading.place_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        expires_in_sessions=1,
    )
    reserved = due.reserved_eur
    check("buy reserved cash", reserved > Decimal("50"))
    check(
        "cash left the balance",
        db.get(Account, account.id).balance_eur == balance_before - reserved,
    )
    due.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    expired = trading.expire_due_orders(db)
    db.expire_all()
    check("exactly the due order expired", [o.id for o in expired] == [due.id])
    check("status is expired", db.get(Order, due.id).status == "expired")
    check(
        "reservation refunded in full",
        db.get(Account, account.id).balance_eur == balance_before,
    )
    check("reserved_eur cleared", db.get(Order, due.id).reserved_eur is None)
    check("the other orders are untouched", db.get(Order, order.id).status == "open")

    print("A due sell order gives back the asset")
    held_before = holding_amount(db, account.id)
    sell = trading.place_order(
        db, account, "BTC-EUR", "sell", "limit", Decimal("2"), None, Decimal("200"),
        time_in_force="day",
    )
    check("asset locked at placement", holding_amount(db, account.id) == held_before - 2)
    sell.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    trading.expire_due_orders(db)
    db.expire_all()
    check("asset returned on expiry", holding_amount(db, account.id) == held_before)
    check("sell marked expired", db.get(Order, sell.id).status == "expired")

    print("A stop-loss can expire too")
    stop = trading.place_order(
        db, account, "BTC-EUR", "sell", "stop_loss", Decimal("1"), None, None,
        Decimal("90"), time_in_force="day",
    )
    check("stop-loss got an expiry", stop.expires_at is not None)
    held_before = holding_amount(db, account.id)
    stop.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    trading.expire_due_orders(db)
    db.expire_all()
    check("stop-loss expired", db.get(Order, stop.id).status == "expired")
    check("stop-loss asset returned", holding_amount(db, account.id) == held_before + 1)

    print("Nothing due means nothing happens")
    check("no-op sweep", trading.expire_due_orders(db) == [])

    print("An expired order can no longer be cancelled")
    expect_error(
        "cancelling an expired order rejected",
        lambda: trading.cancel_order(db, account, due.id),
        "only open orders",
    )

    print("A lapsed order never fills, even before the sweeper reaches it")
    crossing_tick = [{
        "market": "BTC-EUR", "last": Decimal("40"),
        "bid": Decimal("39"), "ask": Decimal("40"),
    }]
    lapsed = trading.place_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        time_in_force="day",
    )
    check("it rests at the current price", lapsed.status == "open")
    lapsed.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    asyncio.run(trading.match_limit_orders(crossing_tick, TestSession))
    db.expire_all()
    check("a crossing tick does not fill it", db.get(Order, lapsed.id).status == "open")
    trading.expire_due_orders(db)
    db.expire_all()
    check("and the sweeper expires it", db.get(Order, lapsed.id).status == "expired")

    print("An order that has not lapsed still fills normally")
    live = trading.place_order(
        db, account, "BTC-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        expires_in_sessions=5,
    )
    asyncio.run(trading.match_limit_orders(crossing_tick, TestSession))
    db.expire_all()
    check("still fills", db.get(Order, live.id).status == "filled")

    print("Stock orders use the exchange calendar")
    mds.get_market.return_value = STOCK
    mds.get_price.return_value = {**PRICE, "market_open": False}
    db.add(Holding(account_id=account.id, asset="AAPL", amount=Decimal("10")))
    db.commit()
    stock_order = trading.place_order(
        db, account, "AAPL-EUR", "buy", "limit", Decimal("1"), None, Decimal("50"),
        time_in_force="day",
    )
    check(
        "a day order on a closed exchange expires at the next close, not tonight",
        stock_order.expires_at > datetime.now(timezone.utc),
        f"(got {stock_order.expires_at})",
    )
    check(
        "and that close is at a NYSE closing time",
        stock_order.expires_at.hour in (20, 21, 18),
        f"(got hour {stock_order.expires_at.hour})",
    )

    db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
