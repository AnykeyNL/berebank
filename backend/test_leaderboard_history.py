"""Standalone verification of the historical leaderboard (in-memory SQLite).

Run: .venv\\Scripts\\python test_leaderboard_history.py
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, PortfolioSnapshot, User
from app.routers.leaderboard import get_leaderboard_history

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def make_user(db, name: str, role: str = "user", active: bool = True) -> User:
    user = User(
        email=f"{name}@t.t", password_hash="x", display_name=name,
        role=role, is_active=active,
    )
    db.add(user)
    db.flush()
    db.add(Account(user_id=user.id, balance_eur=Decimal("0")))
    db.flush()
    db.commit()
    return user


def snapshot(db, user: User, moment: datetime, total: str):
    db.add(PortfolioSnapshot(
        account_id=user.account.id,
        total_value_eur=Decimal(total),
        asset_count=1,
        created_at=moment,
    ))
    db.commit()


def history(user: User, db, days: int = 30, interval: str = "day"):
    return get_leaderboard_history(days=days, interval=interval, user=user, db=db)


NOW = datetime.now(timezone.utc)


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


db = TestSession()
me = make_user(db, "me")
rival = make_user(db, "rival")
manager = make_user(db, "manager", role="bank_manager")
retired = make_user(db, "retired", active=False)

# Three snapshot runs: two on the same day, one a day earlier. Every run
# writes one row per trader with an identical created_at.
DAY_BEFORE = hours_ago(30)
EARLIER_TODAY = hours_ago(6)
LATEST = hours_ago(1)
for moment, mine, theirs in (
    (DAY_BEFORE, "1000", "1200"),
    (EARLIER_TODAY, "1100", "1150"),
    (LATEST, "1300", "1250"),
):
    snapshot(db, me, moment, mine)
    snapshot(db, rival, moment, theirs)
    snapshot(db, manager, moment, "99999")
    snapshot(db, retired, moment, "88888")

print("Daily points read as the closing value of each day")
result = history(me, db)
check("one point per day", len(result.points) == 2, f"(got {len(result.points)})")
check("parameters echoed", result.days == 30 and result.interval == "day")
last = result.points[-1]
check("the last run of the day wins", last.total_eur == Decimal("1300"))
check("rank flipped to first", last.rank == 1)
check("leader total is my own when I lead", last.leader_total_eur == Decimal("1300"))
first = result.points[0]
check("earlier day kept", first.total_eur == Decimal("1000"))
check("and I was second then", first.rank == 2)
check("with the gap visible", first.leader_total_eur == Decimal("1200"))
check("oldest first", first.created_at < last.created_at)

print("Only competitors are counted")
check("manager and disabled account excluded", last.traders == 2, f"(got {last.traders})")

print("Hourly detail keeps every run")
hourly = history(me, db, interval="hour")
check("three runs, three points", len(hourly.points) == 3, f"(got {len(hourly.points)})")
check("interval echoed", hourly.interval == "hour")
check(
    "the intraday dip is visible",
    [p.total_eur for p in hourly.points] == [Decimal("1000"), Decimal("1100"), Decimal("1300")],
    f"(got {[str(p.total_eur) for p in hourly.points]})",
)
check("and its rank", hourly.points[1].rank == 2)

print("The window is respected")
check("one day back drops the older run", len(history(me, db, days=1).points) == 1)

print("Ranking is by value, not by insertion")
newcomer = make_user(db, "newcomer")
snapshot(db, newcomer, LATEST, "5000")
result = history(me, db)
check("a richer trader takes the lead", result.points[-1].rank == 2)
check("leader total follows", result.points[-1].leader_total_eur == Decimal("5000"))
check("trader count grows", result.points[-1].traders == 3)

print("A trader only appears once their own snapshots exist")
fresh = history(newcomer, db)
check("no points before the account existed", len(fresh.points) == 1)
check("and that point is theirs", fresh.points[0].total_eur == Decimal("5000"))

print("Ties share the best rank")
tied = make_user(db, "tied")
snapshot(db, tied, LATEST, "5000")
check("both leaders rank first", history(tied, db).points[-1].rank == 1)

print("An account without snapshots gets an empty history")
silent = make_user(db, "silent")
empty = history(silent, db)
check("no points", empty.points == [])
check("still a well-formed answer", empty.days == 30 and empty.interval == "day")

print("Serialization")
payload = history(me, db).model_dump(mode="json")
point = payload["points"][-1]
check("timestamps carry an explicit UTC offset", point["created_at"].endswith("Z"), point)
check("totals are strings", isinstance(point["total_eur"], str), point)
check("no account or user ids leak", "account_id" not in point and "user_id" not in point)

db.close()

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
