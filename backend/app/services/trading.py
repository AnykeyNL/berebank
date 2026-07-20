"""Trading engine: market order execution, limit order matching, reservations.

Reservation model:
- Open limit BUY: EUR (cost + maker fee at placement) is deducted from the
  balance up front and stored in Order.reserved_eur. On fill the actual fee is
  recomputed (it can only be equal or lower, since volume only grows) and any
  difference is refunded. On cancel the full reservation is refunded.
- Open limit SELL: the base asset amount is deducted from the holding up
  front. On cancel it is returned.
- Open STOP-LOSS (always a sell): reserved like a limit sell. When the live
  bid drops to or below the trigger price the order fills at the live bid and
  pays the taker fee (the fill can be below the trigger on a price gap).
"""
import asyncio
import logging
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MIN_ORDER_EUR
from ..models import Account, Holding, Order, Trade
from .fees import calc_fee, get_30d_volume, get_fee_rates
from .market_data import market_data_service

logger = logging.getLogger("berebank.trading")

AMOUNT_QUANT = Decimal("0.00000001")

# Serializes all order placement/cancellation/matching so balances stay consistent.
trade_lock = asyncio.Lock()

# Markets that currently have open resting orders (limit or stop-loss), so
# the matcher can skip the database for the (many) markets without any.
_open_limit_markets: set[str] = set()

RESTING_ORDER_TYPES = ("limit", "stop_loss")


class TradingError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def load_open_limit_markets(db: Session) -> None:
    markets = db.scalars(
        select(Order.market)
        .where(Order.status == "open", Order.order_type.in_(RESTING_ORDER_TYPES))
        .distinct()
    ).all()
    _open_limit_markets.clear()
    _open_limit_markets.update(markets)


def _get_holding(db: Session, account_id: int, asset: str) -> Holding | None:
    return db.scalar(
        select(Holding).where(Holding.account_id == account_id, Holding.asset == asset)
    )


def _credit_holding(db: Session, account_id: int, asset: str, amount: Decimal) -> None:
    holding = _get_holding(db, account_id, asset)
    if holding is None:
        holding = Holding(account_id=account_id, asset=asset, amount=amount)
        db.add(holding)
    else:
        holding.amount = holding.amount + amount


def _debit_holding(db: Session, account_id: int, asset: str, amount: Decimal) -> None:
    holding = _get_holding(db, account_id, asset)
    available = holding.amount if holding else Decimal("0")
    if available < amount:
        raise TradingError(f"Insufficient {asset} balance: have {available}, need {amount}")
    holding.amount = holding.amount - amount
    if holding.amount == 0:
        db.delete(holding)


def _record_trade(
    db: Session, order: Order, account: Account, amount: Decimal, price: Decimal,
    eur_value: Decimal, fee: Decimal,
) -> None:
    now = datetime.now(timezone.utc)
    order.status = "filled"
    order.fee_paid = fee
    order.filled_price = price
    order.filled_at = now
    db.add(Trade(
        account_id=account.id, order_id=order.id, market=order.market, side=order.side,
        amount=amount, price=price, eur_value=eur_value, fee_eur=fee, created_at=now,
    ))


def place_order(
    db: Session, account: Account, market: str, side: str, order_type: str,
    amount: Decimal | None, amount_quote: Decimal | None, limit_price: Decimal | None,
    trigger_price: Decimal | None = None,
) -> Order:
    market_info = market_data_service.get_market(market)
    if market_info is None:
        raise TradingError(f"Unknown market: {market}")
    price_info = market_data_service.get_price(market)
    if price_info is None or price_info.get("last") is None:
        raise TradingError(f"No live price available for {market} yet, try again shortly")
    # Stock/fund exchanges are closed nights and weekends: market orders are
    # rejected; limit orders may rest and fill when trading resumes.
    if order_type == "market" and price_info.get("market_open") is False:
        raise TradingError(
            f"The exchange for {market} is currently closed. "
            "Place a limit order instead, or try again during trading hours."
        )

    base_asset = market_info["base"]
    volume_30d = get_30d_volume(db, account.id)
    maker_rate, taker_rate = get_fee_rates(volume_30d)

    if order_type == "market":
        return _execute_market_order(
            db, account, market, side, base_asset, amount, amount_quote, price_info, taker_rate
        )
    if amount_quote is not None:
        raise TradingError("amount_quote is only supported for market orders")
    if order_type == "stop_loss":
        if side != "sell":
            raise TradingError("Stop-loss orders can only be sell orders")
        if amount is None or trigger_price is None:
            raise TradingError("Stop-loss orders require both amount and trigger_price")
        return _place_stop_loss_order(
            db, account, market, base_asset, amount, trigger_price, price_info
        )
    # limit order
    if amount is None or limit_price is None:
        raise TradingError("Limit orders require both amount and limit_price")
    return _place_limit_order(
        db, account, market, side, base_asset, amount, limit_price, maker_rate, price_info
    )


def _execute_market_order(
    db: Session, account: Account, market: str, side: str, base_asset: str,
    amount: Decimal | None, amount_quote: Decimal | None, price_info: dict,
    taker_rate: Decimal,
) -> Order:
    if (amount is None) == (amount_quote is None):
        raise TradingError("Market orders require exactly one of amount or amount_quote")

    last = price_info["last"]
    price = (price_info.get("ask") if side == "buy" else price_info.get("bid")) or last

    if amount_quote is not None:
        eur_value = amount_quote
        amount = (amount_quote / price).quantize(AMOUNT_QUANT, rounding=ROUND_DOWN)
    else:
        eur_value = amount * price
    if amount <= 0:
        raise TradingError("Order amount is too small")
    if eur_value < MIN_ORDER_EUR:
        raise TradingError(f"Minimum order value is EUR {MIN_ORDER_EUR}")

    fee = calc_fee(eur_value, taker_rate)
    order = Order(
        account_id=account.id, market=market, side=side, order_type="market",
        amount=amount, amount_quote=amount_quote,
    )

    if side == "buy":
        total = eur_value + fee
        if account.balance_eur < total:
            raise TradingError(
                f"Insufficient EUR balance: need {total:.2f} (incl. {fee:.2f} fee), "
                f"have {account.balance_eur:.2f}"
            )
        account.balance_eur = account.balance_eur - total
        _credit_holding(db, account.id, base_asset, amount)
    else:
        _debit_holding(db, account.id, base_asset, amount)
        account.balance_eur = account.balance_eur + (eur_value - fee)

    db.add(order)
    db.flush()
    _record_trade(db, order, account, amount, price, eur_value, fee)
    db.commit()
    return order


def _place_limit_order(
    db: Session, account: Account, market: str, side: str, base_asset: str,
    amount: Decimal, limit_price: Decimal, maker_rate: Decimal, price_info: dict,
) -> Order:
    eur_value = amount * limit_price
    if eur_value < MIN_ORDER_EUR:
        raise TradingError(f"Minimum order value is EUR {MIN_ORDER_EUR}")

    order = Order(
        account_id=account.id, market=market, side=side, order_type="limit",
        amount=amount, limit_price=limit_price,
    )

    if side == "buy":
        reserve = eur_value + calc_fee(eur_value, maker_rate)
        if account.balance_eur < reserve:
            raise TradingError(
                f"Insufficient EUR balance: need {reserve:.2f} reserved, "
                f"have {account.balance_eur:.2f}"
            )
        account.balance_eur = account.balance_eur - reserve
        order.reserved_eur = reserve
    else:
        _debit_holding(db, account.id, base_asset, amount)

    db.add(order)
    db.commit()
    _open_limit_markets.add(market)

    # Immediately-crossing limit orders fill on the next ticker update; also
    # check right away against the current price.
    _try_fill_limit_order(db, order, price_info)
    db.commit()
    return order


def _place_stop_loss_order(
    db: Session, account: Account, market: str, base_asset: str,
    amount: Decimal, trigger_price: Decimal, price_info: dict,
) -> Order:
    eur_value = amount * trigger_price
    if eur_value < MIN_ORDER_EUR:
        raise TradingError(f"Minimum order value is EUR {MIN_ORDER_EUR}")

    current = price_info.get("bid") or price_info.get("last")
    if current is not None and trigger_price >= current:
        raise TradingError(
            f"Stop-loss trigger price must be below the current price ({current})"
        )

    order = Order(
        account_id=account.id, market=market, side="sell", order_type="stop_loss",
        amount=amount, trigger_price=trigger_price,
    )
    _debit_holding(db, account.id, base_asset, amount)

    db.add(order)
    db.commit()
    _open_limit_markets.add(market)
    return order


def cancel_order(db: Session, account: Account, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None or order.account_id != account.id:
        raise TradingError("Order not found")
    if order.status != "open":
        raise TradingError(f"Order is {order.status}, only open orders can be cancelled")

    base_asset = order.market.split("-")[0]
    if order.side == "buy":
        account.balance_eur = account.balance_eur + (order.reserved_eur or Decimal("0"))
        order.reserved_eur = None
    else:
        _credit_holding(db, account.id, base_asset, order.amount)
    order.status = "cancelled"
    db.commit()

    still_open = db.scalar(
        select(Order.id).where(
            Order.status == "open",
            Order.order_type.in_(RESTING_ORDER_TYPES),
            Order.market == order.market,
        ).limit(1)
    )
    if still_open is None:
        _open_limit_markets.discard(order.market)
    return order


def _try_fill_limit_order(db: Session, order: Order, price_info: dict) -> bool:
    """Fill an open limit order if the market price crosses its limit price."""
    if price_info.get("market_open") is False:
        return False  # stock/fund exchange closed; keep the order resting
    last = price_info.get("last")
    if order.side == "buy":
        market_price = price_info.get("ask") or last
        crossed = market_price is not None and market_price <= order.limit_price
    else:
        market_price = price_info.get("bid") or last
        crossed = market_price is not None and market_price >= order.limit_price
    if not crossed:
        return False

    account = db.get(Account, order.account_id)
    price = order.limit_price  # maker fill at the limit price
    eur_value = order.amount * price
    volume_30d = get_30d_volume(db, account.id)
    maker_rate, _ = get_fee_rates(volume_30d)
    fee = calc_fee(eur_value, maker_rate)
    base_asset = order.market.split("-")[0]

    if order.side == "buy":
        reserve = order.reserved_eur or Decimal("0")
        total = eur_value + fee
        refund = reserve - total
        if refund > 0:
            account.balance_eur = account.balance_eur + refund
        order.reserved_eur = None
        _credit_holding(db, account.id, base_asset, order.amount)
    else:
        account.balance_eur = account.balance_eur + (eur_value - fee)

    _record_trade(db, order, account, order.amount, price, eur_value, fee)
    logger.info("Filled limit order %d: %s %s %s @ %s", order.id, order.side, order.amount, order.market, price)
    return True


def _try_fill_stop_loss(db: Session, order: Order, price_info: dict) -> bool:
    """Execute a stop-loss if the market price has dropped to its trigger.

    Fills at the live bid (taker fee), which can be below the trigger price
    when the market gaps down.
    """
    if price_info.get("market_open") is False:
        return False  # stock/fund exchange closed; keep the order resting
    price = price_info.get("bid") or price_info.get("last")
    if price is None or price > order.trigger_price:
        return False

    account = db.get(Account, order.account_id)
    eur_value = order.amount * price
    volume_30d = get_30d_volume(db, account.id)
    _, taker_rate = get_fee_rates(volume_30d)
    fee = calc_fee(eur_value, taker_rate)

    account.balance_eur = account.balance_eur + (eur_value - fee)
    _record_trade(db, order, account, order.amount, price, eur_value, fee)
    logger.info(
        "Filled stop-loss order %d: sell %s %s @ %s (trigger %s)",
        order.id, order.amount, order.market, price, order.trigger_price,
    )
    return True


def _try_fill_resting_order(db: Session, order: Order, price_info: dict) -> bool:
    if order.order_type == "stop_loss":
        return _try_fill_stop_loss(db, order, price_info)
    return _try_fill_limit_order(db, order, price_info)


async def match_limit_orders(updates: list[dict], session_factory) -> None:
    """Price listener: fill open resting orders (limit and stop-loss) crossed
    by incoming ticker updates."""
    relevant = [u for u in updates if u["market"] in _open_limit_markets]
    if not relevant:
        return
    async with trade_lock:
        db: Session = session_factory()
        try:
            for update in relevant:
                market = update["market"]
                orders = db.scalars(
                    select(Order).where(
                        Order.status == "open",
                        Order.order_type.in_(RESTING_ORDER_TYPES),
                        Order.market == market,
                    )
                ).all()
                any_open_left = False
                for order in orders:
                    if not _try_fill_resting_order(db, order, update):
                        any_open_left = True
                db.commit()
                if not any_open_left:
                    _open_limit_markets.discard(market)
        except Exception:
            db.rollback()
            logger.exception("Limit order matching failed")
        finally:
            db.close()
