from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account, Trade, User
from ..schemas import LeaderboardEntry
from ..security import get_current_user
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
