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

from ..config import AMOUNT_QUANTUM, MIN_ORDER_EUR
from ..models import Account, Holding, Order, Trade
from . import market_calendar
from .fees import calc_fee, get_30d_volume, get_fee_rates
from .market_data import market_data_service
from .payload import plain_decimal as _money

logger = logging.getLogger("berebank.trading")

AMOUNT_QUANT = Decimal(AMOUNT_QUANTUM)
CLIENT_ORDER_ID_MAX_LENGTH = 64
TIME_IN_FORCE = ("gtc", "day", "gtd")
MAX_EXPIRY_SESSIONS = 250  # about a trading year; guards against typos

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


def _check_holding(db: Session, account_id: int, asset: str, amount: Decimal) -> Holding:
    """The holding to debit, or a TradingError when it cannot cover `amount`."""
    holding = _get_holding(db, account_id, asset)
    available = holding.amount if holding else Decimal("0")
    if available < amount:
        raise TradingError(f"Insufficient {asset} balance: have {available}, need {amount}")
    return holding


def _debit_holding(db: Session, account_id: int, asset: str, amount: Decimal) -> None:
    holding = _check_holding(db, account_id, asset, amount)
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


def find_client_order(db: Session, account: Account, client_order_id: str) -> Order | None:
    """The order this account already stored under `client_order_id`, if any."""
    return db.scalar(
        select(Order).where(
            Order.account_id == account.id,
            Order.client_order_id == client_order_id,
        )
    )


def normalize_client_order_id(client_order_id: str | None) -> str | None:
    if client_order_id is None:
        return None
    normalized = client_order_id.strip()
    if not normalized:
        raise TradingError("client_order_id cannot be empty")
    if len(normalized) > CLIENT_ORDER_ID_MAX_LENGTH:
        raise TradingError(
            f"client_order_id can be at most {CLIENT_ORDER_ID_MAX_LENGTH} characters"
        )
    return normalized


def resolve_expiry(
    asset_class: str | None,
    order_type: str,
    time_in_force: str | None,
    expires_at: datetime | None,
    expires_in_sessions: int | None,
    now: datetime | None = None,
) -> tuple[str, datetime | None, int | None]:
    """Turn an expiry intention into a concrete UTC moment.

    Expiry is counted in trading sessions, not wall-clock hours, which is the
    only reading that makes sense across a weekend: a NYSE order placed on
    Saturday with two sessions runs out at Tuesday's close.

    Returns (time_in_force, expires_at, expires_after_sessions).
    """
    has_explicit = expires_at is not None or expires_in_sessions is not None
    if time_in_force is None:
        time_in_force = "gtd" if has_explicit else "gtc"
    time_in_force = time_in_force.lower()
    if time_in_force not in TIME_IN_FORCE:
        raise TradingError(
            f"time_in_force must be one of {', '.join(TIME_IN_FORCE)}"
        )
    if order_type == "market":
        if time_in_force != "gtc" or has_explicit:
            raise TradingError(
                "Expiry applies to resting orders only; a market order fills "
                "or is rejected immediately"
            )
        return "gtc", None, None

    if time_in_force == "gtc":
        if has_explicit:
            raise TradingError(
                "time_in_force 'gtc' never expires; use 'gtd' with expires_at or "
                "expires_in_sessions, or 'day'"
            )
        return "gtc", None, None

    if time_in_force == "day":
        if has_explicit:
            raise TradingError(
                "time_in_force 'day' already expires at the end of the session; "
                "use 'gtd' to set your own moment"
            )
        return "day", market_calendar.advance_sessions(asset_class, 1, now), 1

    if expires_at is not None and expires_in_sessions is not None:
        raise TradingError("Use either expires_at or expires_in_sessions, not both")
    if expires_at is None and expires_in_sessions is None:
        raise TradingError(
            "time_in_force 'gtd' requires expires_at or expires_in_sessions"
        )
    if expires_in_sessions is not None:
        if expires_in_sessions < 1 or expires_in_sessions > MAX_EXPIRY_SESSIONS:
            raise TradingError(
                f"expires_in_sessions must be between 1 and {MAX_EXPIRY_SESSIONS}"
            )
        resolved = market_calendar.advance_sessions(asset_class, expires_in_sessions, now)
        return "gtd", resolved, expires_in_sessions

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expires_at = expires_at.astimezone(timezone.utc)
    if expires_at <= (now or datetime.now(timezone.utc)):
        raise TradingError(f"expires_at must be in the future (got {expires_at.isoformat()})")
    return "gtd", expires_at, None


def _preview(
    *, market: str, side: str, order_type: str, base_asset: str, amount: Decimal,
    price: Decimal, price_basis: str, eur_value: Decimal, fee: Decimal,
    fee_rate: Decimal, fee_type: str, fee_charged_at: str, balance_before: Decimal,
    balance_after: Decimal, reserved_eur: Decimal | None = None,
    locked_amount: Decimal | None = None, fills_immediately: bool = False,
    expiry: tuple[str, datetime | None, int | None] = ("gtc", None, None),
) -> dict:
    """What the order would cost, without placing it.

    Every number is the one the engine would actually use, so an agent can
    check precision, fees and affordability before committing.
    """
    return {
        "valid": True,
        "validated_only": True,
        "market": market,
        "side": side,
        "order_type": order_type,
        "base_asset": base_asset,
        "amount": _money(amount),
        "price": _money(price),
        "price_basis": price_basis,
        "eur_value": _money(eur_value),
        "fee_eur": _money(fee),
        "fee_rate_pct": _money(fee_rate * 100),
        "fee_type": fee_type,
        "fee_charged_at": fee_charged_at,
        "reserved_eur": None if reserved_eur is None else _money(reserved_eur),
        "locked_amount": None if locked_amount is None else _money(locked_amount),
        "balance_eur": _money(balance_before),
        "balance_after_eur": _money(balance_after),
        "fills_immediately": fills_immediately,
        "time_in_force": expiry[0],
        "expires_at": None if expiry[1] is None else expiry[1].isoformat().replace("+00:00", "Z"),
        "expires_after_sessions": expiry[2],
        "minimum_order_eur": str(MIN_ORDER_EUR),
    }


def place_order(
    db: Session, account: Account, market: str, side: str, order_type: str,
    amount: Decimal | None, amount_quote: Decimal | None, limit_price: Decimal | None,
    trigger_price: Decimal | None = None, *, client_order_id: str | None = None,
    validate_only: bool = False, time_in_force: str | None = None,
    expires_at: datetime | None = None, expires_in_sessions: int | None = None,
) -> Order | dict:
    """Place an order, or (with validate_only) report what it would cost.

    A `client_order_id` makes the call idempotent: replaying it returns the
    order stored under that id instead of placing a second one. Resting orders
    can carry an expiry, resolved through the market's trading calendar.
    """
    client_order_id = normalize_client_order_id(client_order_id)
    if client_order_id is not None and not validate_only:
        existing = find_client_order(db, account, client_order_id)
        if existing is not None:
            return existing

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
    expiry = resolve_expiry(
        market_info.get("asset_class"), order_type, time_in_force,
        expires_at, expires_in_sessions,
    )

    if order_type == "market":
        return _execute_market_order(
            db, account, market, side, base_asset, amount, amount_quote, price_info,
            taker_rate, client_order_id=client_order_id, validate_only=validate_only,
        )
    if amount_quote is not None:
        raise TradingError("amount_quote is only supported for market orders")
    if order_type == "stop_loss":
        if side != "sell":
            raise TradingError("Stop-loss orders can only be sell orders")
        if amount is None or trigger_price is None:
            raise TradingError("Stop-loss orders require both amount and trigger_price")
        return _place_stop_loss_order(
            db, account, market, base_asset, amount, trigger_price, price_info,
            taker_rate, client_order_id=client_order_id, validate_only=validate_only,
            expiry=expiry,
        )
    # limit order
    if amount is None or limit_price is None:
        raise TradingError("Limit orders require both amount and limit_price")
    return _place_limit_order(
        db, account, market, side, base_asset, amount, limit_price, maker_rate, price_info,
        client_order_id=client_order_id, validate_only=validate_only, expiry=expiry,
    )


def preview_order(
    db: Session, account: Account, market: str, side: str, order_type: str,
    amount: Decimal | None, amount_quote: Decimal | None, limit_price: Decimal | None,
    trigger_price: Decimal | None = None, *, time_in_force: str | None = None,
    expires_at: datetime | None = None, expires_in_sessions: int | None = None,
) -> dict:
    """Run every check `place_order` runs and report the cost, placing nothing."""
    return place_order(
        db, account, market, side, order_type, amount, amount_quote, limit_price,
        trigger_price, validate_only=True, time_in_force=time_in_force,
        expires_at=expires_at, expires_in_sessions=expires_in_sessions,
    )


def _execute_market_order(
    db: Session, account: Account, market: str, side: str, base_asset: str,
    amount: Decimal | None, amount_quote: Decimal | None, price_info: dict,
    taker_rate: Decimal, *, client_order_id: str | None = None,
    validate_only: bool = False,
) -> Order | dict:
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
    total = eur_value + fee
    if side == "buy":
        if account.balance_eur < total:
            raise TradingError(
                f"Insufficient EUR balance: need {total:.2f} (incl. {fee:.2f} fee), "
                f"have {account.balance_eur:.2f}"
            )
    else:
        _check_holding(db, account.id, base_asset, amount)

    if validate_only:
        return _preview(
            market=market, side=side, order_type="market", base_asset=base_asset,
            amount=amount, price=price, price_basis="ask" if side == "buy" else "bid",
            eur_value=eur_value, fee=fee, fee_rate=taker_rate, fee_type="taker",
            fee_charged_at="fill", balance_before=account.balance_eur,
            balance_after=account.balance_eur - total if side == "buy"
            else account.balance_eur + (eur_value - fee),
            fills_immediately=True,
        )

    order = Order(
        account_id=account.id, client_order_id=client_order_id, market=market, side=side,
        order_type="market", amount=amount, amount_quote=amount_quote,
    )

    if side == "buy":
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
    *, client_order_id: str | None = None, validate_only: bool = False,
    expiry: tuple[str, datetime | None, int | None] = ("gtc", None, None),
) -> Order | dict:
    eur_value = amount * limit_price
    if eur_value < MIN_ORDER_EUR:
        raise TradingError(f"Minimum order value is EUR {MIN_ORDER_EUR}")

    fee = calc_fee(eur_value, maker_rate)
    reserve = eur_value + fee
    if side == "buy":
        if account.balance_eur < reserve:
            raise TradingError(
                f"Insufficient EUR balance: need {reserve:.2f} reserved, "
                f"have {account.balance_eur:.2f}"
            )
    else:
        _check_holding(db, account.id, base_asset, amount)

    if validate_only:
        buying = side == "buy"
        return _preview(
            market=market, side=side, order_type="limit", base_asset=base_asset,
            amount=amount, price=limit_price, price_basis="limit_price",
            eur_value=eur_value, fee=fee, fee_rate=maker_rate, fee_type="maker",
            fee_charged_at="placement" if buying else "fill",
            balance_before=account.balance_eur,
            balance_after=account.balance_eur - reserve if buying else account.balance_eur,
            reserved_eur=reserve if buying else None,
            locked_amount=None if buying else amount,
            fills_immediately=_limit_order_crosses(side, limit_price, price_info),
            expiry=expiry,
        )

    order = Order(
        account_id=account.id, client_order_id=client_order_id, market=market, side=side,
        order_type="limit", amount=amount, limit_price=limit_price,
        time_in_force=expiry[0], expires_at=expiry[1], expires_after_sessions=expiry[2],
    )

    if side == "buy":
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
    amount: Decimal, trigger_price: Decimal, price_info: dict, taker_rate: Decimal,
    *, client_order_id: str | None = None, validate_only: bool = False,
    expiry: tuple[str, datetime | None, int | None] = ("gtc", None, None),
) -> Order | dict:
    eur_value = amount * trigger_price
    if eur_value < MIN_ORDER_EUR:
        raise TradingError(f"Minimum order value is EUR {MIN_ORDER_EUR}")

    current = price_info.get("bid") or price_info.get("last")
    if current is not None and trigger_price >= current:
        raise TradingError(
            f"Stop-loss trigger price must be below the current price ({current})"
        )
    _check_holding(db, account.id, base_asset, amount)

    if validate_only:
        return _preview(
            market=market, side="sell", order_type="stop_loss", base_asset=base_asset,
            amount=amount, price=trigger_price, price_basis="trigger_price",
            eur_value=eur_value, fee=calc_fee(eur_value, taker_rate), fee_rate=taker_rate,
            fee_type="taker", fee_charged_at="fill", balance_before=account.balance_eur,
            balance_after=account.balance_eur, locked_amount=amount, expiry=expiry,
        )

    order = Order(
        account_id=account.id, client_order_id=client_order_id, market=market, side="sell",
        order_type="stop_loss", amount=amount, trigger_price=trigger_price,
        time_in_force=expiry[0], expires_at=expiry[1], expires_after_sessions=expiry[2],
    )
    _debit_holding(db, account.id, base_asset, amount)

    db.add(order)
    db.commit()
    _open_limit_markets.add(market)
    return order


def _release_order(db: Session, account: Account, order: Order, status: str) -> None:
    """Give back what an open order reserved and close it with `status`.

    Cancelling and expiring differ only in the label; the reservation has to
    come back either way.
    """
    if order.side == "buy":
        account.balance_eur = account.balance_eur + (order.reserved_eur or Decimal("0"))
        order.reserved_eur = None
    else:
        _credit_holding(db, account.id, order.market.split("-")[0], order.amount)
    order.status = status


def _forget_market_if_idle(db: Session, market: str) -> None:
    still_open = db.scalar(
        select(Order.id).where(
            Order.status == "open",
            Order.order_type.in_(RESTING_ORDER_TYPES),
            Order.market == market,
        ).limit(1)
    )
    if still_open is None:
        _open_limit_markets.discard(market)


def cancel_order(db: Session, account: Account, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None or order.account_id != account.id:
        raise TradingError("Order not found")
    if order.status != "open":
        raise TradingError(f"Order is {order.status}, only open orders can be cancelled")

    _release_order(db, account, order, "cancelled")
    db.commit()
    _forget_market_if_idle(db, order.market)
    return order


def expire_due_orders(db: Session, now: datetime | None = None) -> list[Order]:
    """Close every open order whose expiry has passed, releasing its reservation.

    A hook in the price matcher would not do: a stock stops ticking when its
    exchange closes, and that is exactly when a day order has to expire.
    """
    now = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(Order).where(
            Order.status == "open",
            Order.expires_at.is_not(None),
            Order.expires_at <= now,
        )
    ).all()
    if not due:
        return []

    expired = []
    for order in due:
        account = db.get(Account, order.account_id)
        if account is None:
            continue
        _release_order(db, account, order, "expired")
        expired.append(order)
    db.commit()
    for market in {order.market for order in expired}:
        _forget_market_if_idle(db, market)
    for order in expired:
        logger.info(
            "Expired order %d (%s %s %s, time_in_force=%s)",
            order.id, order.side, order.amount, order.market, order.time_in_force,
        )
    return expired


def _limit_order_crosses(side: str, limit_price: Decimal, price_info: dict) -> bool:
    """Whether the live price already satisfies this limit price."""
    if price_info.get("market_open") is False:
        return False  # stock/fund exchange closed; keep the order resting
    last = price_info.get("last")
    if side == "buy":
        market_price = price_info.get("ask") or last
        return market_price is not None and market_price <= limit_price
    market_price = price_info.get("bid") or last
    return market_price is not None and market_price >= limit_price


def _try_fill_limit_order(db: Session, order: Order, price_info: dict) -> bool:
    """Fill an open limit order if the market price crosses its limit price."""
    if not _limit_order_crosses(order.side, order.limit_price, price_info):
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


def _has_lapsed(order: Order, now: datetime) -> bool:
    if order.expires_at is None:
        return False
    expires_at = order.expires_at
    if expires_at.tzinfo is None:  # SQLite hands back naive timestamps
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


def _try_fill_resting_order(db: Session, order: Order, price_info: dict) -> bool:
    # The sweeper runs once a minute; never fill an order it has not reached yet.
    if _has_lapsed(order, datetime.now(timezone.utc)):
        return False
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
