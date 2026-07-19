from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account, Holding, Order, Trade, User
from ..schemas import LeaderboardEntry
from ..security import get_current_user
from ..services.market_data import market_data_service

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

    reserved_by_account: dict[int, Decimal] = {}
    for account_id, reserved in db.execute(
        select(Order.account_id, Order.reserved_eur).where(Order.status == "open")
    ).all():
        if reserved is not None:
            reserved_by_account[account_id] = reserved_by_account.get(account_id, Decimal("0")) + reserved

    holdings_by_account: dict[int, Decimal] = {}
    for account_id, asset, amount in db.execute(
        select(Holding.account_id, Holding.asset, Holding.amount).where(Holding.amount > 0)
    ).all():
        price_info = market_data_service.get_price(f"{asset}-EUR")
        price = price_info.get("last") if price_info else None
        if price is not None:
            value = amount * price
            holdings_by_account[account_id] = holdings_by_account.get(account_id, Decimal("0")) + value

    entries = []
    for user_id, display_name, account_id, balance in rows:
        cash = balance + reserved_by_account.get(account_id, Decimal("0"))
        assets = holdings_by_account.get(account_id, Decimal("0"))
        entries.append(LeaderboardEntry(
            user_id=user_id,
            display_name=display_name,
            trades=trade_counts.get(account_id, 0),
            cash_eur=cash,
            assets_eur=assets,
            total_eur=cash + assets,
        ))

    entries.sort(key=lambda e: e.total_eur, reverse=True)
    return entries
