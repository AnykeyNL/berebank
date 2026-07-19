from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Holding, Order, User
from ..schemas import FeeTierOut, HoldingOut, PortfolioOut
from ..security import get_current_user
from ..services.fees import get_30d_volume, get_fee_rates
from ..services.market_data import market_data_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioOut)
def get_portfolio(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = user.account

    holdings = [
        h for h in db.scalars(select(Holding).where(Holding.account_id == account.id)).all()
        if h.amount > 0
    ]

    open_orders = db.scalars(
        select(Order).where(Order.account_id == account.id, Order.status == "open")
    ).all()
    reserved = sum(
        (o.reserved_eur or Decimal("0") for o in open_orders), Decimal("0")
    )
    # Base assets reserved in open limit sells were debited from the holding
    # at placement but still belong to the user until the order fills.
    reserved_assets: dict[str, Decimal] = {}
    for o in open_orders:
        if o.side == "sell":
            asset = o.market.split("-")[0]
            reserved_assets[asset] = reserved_assets.get(asset, Decimal("0")) + o.amount

    available = {h.asset: h.amount for h in holdings}
    holding_rows: list[HoldingOut] = []
    holdings_value = Decimal("0")
    for asset in sorted(available.keys() | reserved_assets.keys()):
        amount = available.get(asset, Decimal("0"))
        reserved_amount = reserved_assets.get(asset, Decimal("0"))
        market = f"{asset}-EUR"
        market_info = market_data_service.get_market(market)
        price_info = market_data_service.get_price(market)
        price = price_info.get("last") if price_info else None
        value = ((amount + reserved_amount) * price) if price is not None else None
        if value is not None:
            holdings_value += value
        holding_rows.append(HoldingOut(
            asset=asset,
            amount=amount,
            reserved=reserved_amount,
            market=market if market_info else None,
            name=market_info.get("name") if market_info else None,
            listing=market_info.get("listing") if market_info else None,
            current_price=price,
            eur_value=value,
        ))

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
