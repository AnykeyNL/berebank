from collections import defaultdict, deque
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, Trade, User
from ..schemas import OrderCreate, OrderOut, TradeOut, TradePnlOut
from ..security import get_current_user
from ..services import trading
from ..services.trading import TradingError, trade_lock

router = APIRouter(tags=["trading"])


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async with trade_lock:
        try:
            order = trading.place_order(
                db, user.account, body.market.upper(), body.side, body.order_type,
                body.amount, body.amount_quote, body.limit_price, body.trigger_price,
            )
        except TradingError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)
    return order


@router.get("/orders", response_model=list[OrderOut])
def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Order).where(Order.account_id == user.account.id)
    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
    return db.scalars(stmt.order_by(Order.created_at.desc()).limit(200)).all()


@router.delete("/orders/{order_id}", response_model=OrderOut)
async def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async with trade_lock:
        try:
            return trading.cancel_order(db, user.account, order_id)
        except TradingError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message)


@router.get("/trades", response_model=list[TradeOut])
def list_trades(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Trade)
        .where(Trade.account_id == user.account.id)
        .order_by(Trade.created_at.desc())
        .limit(200)
    ).all()


@router.get("/trades/history", response_model=list[TradePnlOut])
def trade_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """All trades with FIFO profit/loss for sells.

    Each buy creates a lot with a unit cost that includes the fee. Sells
    consume lots oldest-first; profit is net proceeds (after fee) minus the
    cost of the consumed lots. Holding duration is the amount-weighted age of
    those lots at the moment of sale.
    """
    trades = db.scalars(
        select(Trade)
        .where(Trade.account_id == user.account.id)
        .order_by(Trade.created_at.asc(), Trade.id.asc())
    ).all()

    # asset -> lots of [remaining_amount, unit_cost_eur, buy_timestamp]
    lots: dict[str, deque] = defaultdict(deque)
    results: list[TradePnlOut] = []

    for t in trades:
        row = TradePnlOut.model_validate(t)
        asset = t.market.split("-")[0]
        if t.side == "buy":
            unit_cost = (t.eur_value + t.fee_eur) / t.amount
            lots[asset].append([t.amount, unit_cost, t.created_at])
        else:
            remaining = t.amount
            cost = Decimal("0")
            weighted_ts = 0.0
            while remaining > 0 and lots[asset]:
                lot = lots[asset][0]
                take = min(lot[0], remaining)
                cost += take * lot[1]
                weighted_ts += float(take) * lot[2].timestamp()
                lot[0] -= take
                remaining -= take
                if lot[0] == 0:
                    lots[asset].popleft()
            matched = t.amount - remaining
            if matched == t.amount and cost > 0:
                proceeds = t.eur_value - t.fee_eur
                pnl = proceeds - cost
                row.pnl_eur = pnl.quantize(Decimal("0.01"))
                row.pnl_pct = (pnl / cost * 100).quantize(Decimal("0.01"))
                avg_buy_ts = weighted_ts / float(t.amount)
                row.held_seconds = max(0.0, t.created_at.timestamp() - avg_buy_ts)
        results.append(row)

    results.reverse()  # newest first
    return results
