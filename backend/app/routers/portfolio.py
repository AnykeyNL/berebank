from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Holding, Order, User
from ..schemas import FeeTierOut, HoldingOut, PortfolioOut
from ..security import get_current_user
from ..services.bitvavo import bitvavo_service
from ..services.fees import get_30d_volume, get_fee_rates

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioOut)
def get_portfolio(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = user.account

    holdings = [
        h for h in db.scalars(select(Holding).where(Holding.account_id == account.id)).all()
        if h.amount > 0
    ]

    holding_rows: list[HoldingOut] = []
    holdings_value = Decimal("0")
    for h in sorted(holdings, key=lambda h: h.asset):
        market = f"{h.asset}-EUR"
        price_info = bitvavo_service.get_price(market)
        price = price_info.get("last") if price_info else None
        value = (h.amount * price) if price is not None else None
        if value is not None:
            holdings_value += value
        holding_rows.append(HoldingOut(
            asset=h.asset,
            amount=h.amount,
            market=market if market in bitvavo_service.markets else None,
            current_price=price,
            eur_value=value,
        ))

    reserved = sum(
        (o.reserved_eur or Decimal("0") for o in db.scalars(
            select(Order).where(Order.account_id == account.id, Order.status == "open")
        ).all()),
        Decimal("0"),
    )

    volume = get_30d_volume(db, account.id)
    maker, taker = get_fee_rates(volume)

    return PortfolioOut(
        balance_eur=account.balance_eur,
        reserved_eur=reserved,
        holdings=holding_rows,
        holdings_value_eur=holdings_value,
        total_value_eur=account.balance_eur + reserved + holdings_value,
        fee_tier=FeeTierOut(
            volume_30d_eur=volume,
            maker_pct=maker * 100,
            taker_pct=taker * 100,
        ),
    )
