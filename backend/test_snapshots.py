"""Standalone verification of portfolio value snapshots (in-memory SQLite).

Run: .venv\\Scripts\\python test_snapshots.py
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Holding, Order, PortfolioSnapshot, User, utcnow
from app.routers.portfolio import get_portfolio_history
from app.services.snapshots import record_snapshots
from app.services.valuation import compute_account_valuations

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

PRICES = {
    "BTC-EUR": {"last": Decimal("100")},
    "ETH-EUR": {"last": Decimal("10")},
    # XYZ-EUR intentionally has no live price
}

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def make_user(db, email: str, balance: Decimal, *, role="user", is_active=True) -> User:
    user = User(email=email, password_hash="x", display_name=email, role=role, is_active=is_active)
    db.add(user)
    db.flush()
    db.add(Account(user_id=user.id, balance_eur=balance))
    db.commit()
    return user


with patch("app.services.valuation.market_data_service") as mds:
    mds.get_price.side_effect = lambda market: PRICES.get(market)

    db = TestSession()
    trader = make_user(db, "trader@t.t", Decimal("1000"))
    inactive = make_user(db, "inactive@t.t", Decimal("500"), is_active=False)
    admin = make_user(db, "admin@t.t", Decimal("0"), role="bank_manager")

    account = trader.account
    db.add(Holding(account_id=account.id, asset="BTC", amount=Decimal("1")))
    db.add(Holding(account_id=account.id, asset="XYZ", amount=Decimal("2")))  # unpriced
    db.add(Holding(account_id=account.id, asset="ZERO", amount=Decimal("0")))  # sold out
    # Open limit sell: 0.5 ETH locked (debited from holding at placement).
    db.add(Order(account_id=account.id, market="ETH-EUR", side="sell", order_type="limit",
                 amount=Decimal("0.5"), limit_price=Decimal("12")))
    # Open limit buy: EUR 50 reserved.
    db.add(Order(account_id=account.id, market="BTC-EUR", side="buy", order_type="limit",
                 amount=Decimal("0.5"), limit_price=Decimal("95"), reserved_eur=Decimal("50")))
    db.commit()

    print("Valuation:")
    valuations = compute_account_valuations(
        db, {account.id: account.balance_eur, admin.account.id: Decimal("0")}
    )
    v = valuations[account.id]
    check("cash includes reserved EUR", v.cash_eur == Decimal("1050"), f"(got {v.cash_eur})")
    check("assets: 1 BTC@100 + 0.5 ETH@10 locked in sell", v.assets_eur == Decimal("105"),
          f"(got {v.assets_eur})")
    check("unpriced asset contributes no value", "XYZ" in v.assets and v.assets_eur == Decimal("105"))
    check("asset count = BTC, XYZ, ETH (not ZERO)", v.asset_count == 3, f"(got {v.assets})")
    check("total = cash + assets", v.total_eur == Decimal("1155"), f"(got {v.total_eur})")

    print("Recorder:")
    count = record_snapshots(db)
    rows = db.scalars(select(PortfolioSnapshot)).all()
    check("one snapshot per active non-admin account", count == 1 and len(rows) == 1)
    snap = rows[0]
    check("snapshot belongs to the trader", snap.account_id == account.id)
    check("snapshot total value", snap.total_value_eur == Decimal("1155"), f"(got {snap.total_value_eur})")
    check("snapshot asset count", snap.asset_count == 3, f"(got {snap.asset_count})")
    check("no snapshot for inactive user",
          all(r.account_id != inactive.account.id for r in rows))

    print("Pruning:")
    db.add(PortfolioSnapshot(account_id=account.id, total_value_eur=Decimal("1"),
                             asset_count=0, created_at=utcnow() - timedelta(days=200)))
    db.add(PortfolioSnapshot(account_id=account.id, total_value_eur=Decimal("2"),
                             asset_count=0, created_at=utcnow() - timedelta(days=100)))
    db.commit()
    record_snapshots(db)
    remaining = db.scalars(select(PortfolioSnapshot)).all()
    check("row older than 180 days pruned",
          all(r.total_value_eur != Decimal("1") for r in remaining))
    check("row within 180 days kept",
          any(r.total_value_eur == Decimal("2") for r in remaining))

    print("History endpoint:")
    db.add(PortfolioSnapshot(account_id=account.id, total_value_eur=Decimal("3"),
                             asset_count=1, created_at=utcnow() - timedelta(days=40)))
    other = make_user(db, "other@t.t", Decimal("0"))
    db.add(PortfolioSnapshot(account_id=other.account.id, total_value_eur=Decimal("9"),
                             asset_count=0))
    db.commit()
    history = get_portfolio_history(user=trader, db=db)
    check("only own snapshots returned",
          all(s.account_id == account.id for s in history))
    check("older than 30 days excluded",
          all(s.total_value_eur != Decimal("3") for s in history))
    check("100-day-old row retained but not shown",
          all(s.total_value_eur != Decimal("2") for s in history))
    check("oldest first ordering",
          [s.created_at for s in history] == sorted(s.created_at for s in history))
    check("recent snapshots included", len(history) >= 2, f"(got {len(history)})")

    print("MCP serialization:")
    from app.schemas import PortfolioSnapshotOut
    payload = PortfolioSnapshotOut.model_validate(history[0]).model_dump(mode="json")
    check("snapshot serializes for MCP",
          set(payload) == {"created_at", "total_value_eur", "asset_count"}
          and isinstance(payload["total_value_eur"], str),
          f"(got {payload})")

    db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
