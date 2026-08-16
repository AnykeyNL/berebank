from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account, PortfolioSnapshot, Trade, User, utcnow
from ..schemas import LeaderboardEntry, LeaderboardHistoryOut, LeaderboardHistoryPoint
from ..security import get_current_user
from ..services.snapshots import RETENTION_DAYS
from ..services.valuation import compute_account_valuations

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", response_model=list[LeaderboardEntry])
def get_leaderboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """All active users ranked by total account value (cash + reserved + holdings).

    Uses the same valuation as the portfolio page: holdings at the live last
    price, EUR reserved for open limit buys counted as cash.
    """
    rows = db.execute(
        select(User.id, User.display_name, Account.id, Account.balance_eur)
        .join(Account, Account.user_id == User.id)
        .where(User.is_active, User.role != "bank_manager")
    ).all()

    trade_counts = dict(db.execute(
        select(Trade.account_id, func.count(Trade.id)).group_by(Trade.account_id)
    ).all())

    valuations = compute_account_valuations(
        db, {account_id: balance for _, _, account_id, balance in rows}
    )

    entries = []
    for user_id, display_name, account_id, _balance in rows:
        valuation = valuations[account_id]
        entries.append(LeaderboardEntry(
            user_id=user_id,
            display_name=display_name,
            trades=trade_counts.get(account_id, 0),
            cash_eur=valuation.cash_eur,
            assets_eur=valuation.assets_eur,
            total_eur=valuation.total_eur,
        ))

    entries.sort(key=lambda e: e.total_eur, reverse=True)
    return entries


def _bucket(moment: datetime, interval: str) -> datetime:
    if moment.tzinfo is None:  # SQLite returns naive timestamps
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    if interval == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/history", response_model=LeaderboardHistoryOut)
def get_leaderboard_history(
    days: int = Query(default=30, ge=1, le=RETENTION_DAYS),
    interval: str = Query(default="day", pattern="^(hour|day)$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The user's rank over time, from the hourly account-value snapshots.

    Every snapshot run writes one row per active trader with an identical
    `created_at`, so ranking a past moment is an ordering, not a
    reconstruction. Snapshots are kept for 180 days and hold only the total,
    which is why the cash/assets split and the trade count stay point-in-time.
    """
    since = utcnow() - timedelta(days=days)
    rows = db.execute(
        select(
            PortfolioSnapshot.created_at,
            PortfolioSnapshot.account_id,
            PortfolioSnapshot.total_value_eur,
        )
        .join(Account, Account.id == PortfolioSnapshot.account_id)
        .join(User, User.id == Account.user_id)
        .where(
            PortfolioSnapshot.created_at >= since,
            User.is_active,
            User.role != "bank_manager",
        )
        .order_by(PortfolioSnapshot.created_at)
    ).all()

    # One run per bucket: the last one, so a day reads as its closing value.
    runs: dict[datetime, list[tuple[int, object]]] = {}
    latest: dict[datetime, datetime] = {}
    for created_at, account_id, total in rows:
        runs.setdefault(created_at, []).append((account_id, total))
        bucket = _bucket(created_at, interval)
        if bucket not in latest or created_at > latest[bucket]:
            latest[bucket] = created_at

    points = []
    for created_at in sorted(latest.values()):
        totals = runs[created_at]
        mine = next((t for account_id, t in totals if account_id == user.account.id), None)
        if mine is None:  # the account had not been created yet
            continue
        ordered = sorted((t for _, t in totals), reverse=True)
        points.append(LeaderboardHistoryPoint(
            created_at=created_at,
            rank=ordered.index(mine) + 1,
            total_eur=mine,
            leader_total_eur=ordered[0],
            traders=len(ordered),
        ))

    return LeaderboardHistoryOut(days=days, interval=interval, points=points)
