"""Bulk account valuation shared by the leaderboard and the portfolio
snapshot recorder, so every feature agrees on what "total value" means.

Holdings and assets locked in open sell orders are valued at the live last
price. EUR reserved for open limit buys counts as cash. Assets without a
live price contribute nothing to the value but still count as held assets.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Holding, Order
from .market_data import market_data_service


@dataclass
class AccountValuation:
    balance_eur: Decimal = Decimal("0")
    reserved_eur: Decimal = Decimal("0")  # EUR reserved for open limit buys
    assets_eur: Decimal = Decimal("0")
    assets: set[str] = field(default_factory=set)

    @property
    def cash_eur(self) -> Decimal:
        return self.balance_eur + self.reserved_eur

    @property
    def total_eur(self) -> Decimal:
        return self.cash_eur + self.assets_eur

    @property
    def asset_count(self) -> int:
        return len(self.assets)


def compute_account_valuations(
    db: Session, balances: dict[int, Decimal]
) -> dict[int, AccountValuation]:
    """Value the given accounts (account_id -> balance_eur) in one bulk pass."""
    valuations = {
        account_id: AccountValuation(balance_eur=balance)
        for account_id, balance in balances.items()
    }

    def _add_asset(account_id: int, asset: str, amount: Decimal) -> None:
        valuation = valuations.get(account_id)
        if valuation is None:
            return
        valuation.assets.add(asset)
        price_info = market_data_service.get_price(f"{asset}-EUR")
        price = price_info.get("last") if price_info else None
        if price is not None:
            valuation.assets_eur += amount * price

    for account_id, asset, amount in db.execute(
        select(Holding.account_id, Holding.asset, Holding.amount).where(Holding.amount > 0)
    ).all():
        _add_asset(account_id, asset, amount)

    # Assets reserved in open limit sell orders still belong to the user.
    for account_id, market, amount in db.execute(
        select(Order.account_id, Order.market, Order.amount).where(
            Order.status == "open", Order.side == "sell"
        )
    ).all():
        _add_asset(account_id, market.split("-")[0], amount)

    for account_id, reserved in db.execute(
        select(Order.account_id, Order.reserved_eur).where(Order.status == "open")
    ).all():
        valuation = valuations.get(account_id)
        if valuation is not None and reserved is not None:
            valuation.reserved_eur += reserved

    return valuations
