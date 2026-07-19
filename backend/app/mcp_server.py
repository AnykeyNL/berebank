"""MCP server exposing market data, portfolio and (optionally) trading.

Served over the Streamable HTTP transport at /mcp, protected by the OAuth 2.1
authorization server in oauth.py. Read tools are available to every active
user; place_order and cancel_order additionally require the user's
``mcp_trading_enabled`` profile setting, checked on every call so switching it
off takes effect immediately.

Tools reuse the REST layer's functions directly, so behaviour (validation,
fees, limits, response shapes) is identical to the web app.
"""
import logging
from decimal import Decimal, InvalidOperation

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from sqlalchemy.orm import Session

from .config import PUBLIC_URL
from .database import SessionLocal
from .models import User
from .oauth import oauth_provider
from .routers.markets import get_candles as _get_candles
from .routers.markets import list_markets as _list_markets
from .routers.orders import list_orders as _list_orders
from .routers.orders import list_trades as _list_trades
from .routers.orders import trade_history as _trade_history
from .routers.portfolio import get_portfolio as _get_portfolio
from .schemas import OrderOut
from .services import trading
from .services.trading import TradingError, trade_lock

logger = logging.getLogger("berebank.mcp")

mcp = FastMCP(
    "de BereBank",
    instructions=(
        "de BereBank is a simulated exchange: users trade with paper money in EUR "
        "against live market data (crypto via Bitvavo; US and Dutch stocks and funds "
        "via Twelve Data), with realistic maker/taker fees. Amounts and prices are "
        "decimal numbers serialized as strings. Stock/fund market orders are rejected "
        "while the exchange is closed. Placing or cancelling orders requires the user "
        "to have enabled trading via MCP in their BereBank profile."
    ),
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(PUBLIC_URL),
        resource_server_url=AnyHttpUrl(f"{PUBLIC_URL}/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["read", "trade"],
            default_scopes=["read", "trade"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
    # nginx terminates TLS and proxies with the public Host header; the SDK's
    # localhost-only DNS-rebinding default would reject those requests.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    stateless_http=True,
    json_response=True,
)


def _current_user(db: Session) -> User:
    token = get_access_token()
    if token is None or token.subject is None:
        raise ToolError("Not authenticated")
    user = db.get(User, int(token.subject))
    if user is None or not user.is_active:
        raise ToolError("User account not found or deactivated")
    return user


def _require_trading(user: User) -> None:
    if not user.mcp_trading_enabled:
        raise ToolError(
            "Trading via MCP is disabled in your profile. Enable it on your "
            "BereBank profile page (MCP access section) to place or cancel orders."
        )


def _parse_decimal(value: str | float | int | None, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ToolError(f"Invalid decimal value for {field}: {value!r}")


@mcp.tool()
def list_markets(filter: str | None = None, asset_class: str | None = None) -> list[dict]:
    """List EUR markets with live prices (last/bid/ask), 24h change and volume.

    Markets cover crypto plus US/Dutch stocks and funds; each row has an
    asset_class of "crypto", "stock" or "fund" (stocks/funds also carry a
    market_open flag). Optionally filter by asset_class and/or by a
    case-insensitive substring of the market symbol, e.g. "BTC" matches
    BTC-EUR. Prices are EUR decimals as strings.
    """
    if asset_class is not None and asset_class not in ("crypto", "stock", "fund"):
        raise ToolError('asset_class must be "crypto", "stock" or "fund"')
    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _list_markets(user=user, asset_class=asset_class)
    finally:
        db.close()
    if filter:
        needle = filter.upper()
        rows = [m for m in rows if needle in m.market]
    return [m.model_dump(mode="json") for m in rows]


@mcp.tool()
async def get_candles(market: str) -> list[list]:
    """Get the last day of 15-minute OHLCV candles for a market (e.g. BTC-EUR).

    Returns a list of [timestamp_ms, open, high, low, close, volume], oldest first.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
    finally:
        db.close()
    try:
        return await _get_candles(market, user=user)
    except Exception as exc:
        raise ToolError(_http_detail(exc))


@mcp.tool()
def get_portfolio() -> dict:
    """Get the user's portfolio: EUR cash balance, reserved funds, crypto holdings
    with live valuation, total account value and current fee tier."""
    db = SessionLocal()
    try:
        user = _current_user(db)
        return _get_portfolio(user=user, db=db).model_dump(mode="json")
    finally:
        db.close()


@mcp.tool()
def list_orders(status: str | None = None) -> list[dict]:
    """List the user's orders, newest first (max 200).

    Optionally filter by status: "open", "filled" or "cancelled".
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _list_orders(status_filter=status, user=user, db=db)
        return [OrderOut.model_validate(o).model_dump(mode="json") for o in rows]
    finally:
        db.close()


@mcp.tool()
def list_trades() -> list[dict]:
    """List the user's executed trades, newest first (max 200)."""
    from .schemas import TradeOut

    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _list_trades(user=user, db=db)
        return [TradeOut.model_validate(t).model_dump(mode="json") for t in rows]
    finally:
        db.close()


@mcp.tool()
def get_trade_history() -> list[dict]:
    """List all trades with realized profit/loss for sells (FIFO cost basis),
    newest first. Sell trades include pnl_eur, pnl_pct and held_seconds."""
    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _trade_history(user=user, db=db)
        return [r.model_dump(mode="json") for r in rows]
    finally:
        db.close()


@mcp.tool()
async def place_order(
    market: str,
    side: str,
    order_type: str,
    amount: str | None = None,
    amount_quote: str | None = None,
    limit_price: str | None = None,
) -> dict:
    """Place an order. Requires trading via MCP to be enabled in the user's profile.

    Args:
        market: Market symbol, e.g. "BTC-EUR".
        side: "buy" or "sell".
        order_type: "market" (fills immediately at live price, taker fee) or
            "limit" (fills when the price crosses limit_price, maker fee).
        amount: Amount of the base asset (crypto), as a decimal string.
        amount_quote: EUR amount to spend/receive; market orders only.
            Market orders take exactly one of amount or amount_quote.
        limit_price: Limit price in EUR; required for limit orders (together
            with amount).

    Fees are charged in EUR. Minimum order value is EUR 5.
    """
    if side not in ("buy", "sell"):
        raise ToolError('side must be "buy" or "sell"')
    if order_type not in ("market", "limit"):
        raise ToolError('order_type must be "market" or "limit"')
    db = SessionLocal()
    try:
        user = _current_user(db)
        _require_trading(user)
        async with trade_lock:
            try:
                order = trading.place_order(
                    db,
                    user.account,
                    market.upper(),
                    side,
                    order_type,
                    _parse_decimal(amount, "amount"),
                    _parse_decimal(amount_quote, "amount_quote"),
                    _parse_decimal(limit_price, "limit_price"),
                )
            except TradingError as exc:
                db.rollback()
                raise ToolError(exc.message)
        logger.info("MCP order placed by %s: %s", user.email, order.id)
        return OrderOut.model_validate(order).model_dump(mode="json")
    finally:
        db.close()


@mcp.tool()
async def cancel_order(order_id: int) -> dict:
    """Cancel one of the user's open orders. Requires trading via MCP to be
    enabled in the user's profile."""
    db = SessionLocal()
    try:
        user = _current_user(db)
        _require_trading(user)
        async with trade_lock:
            try:
                order = trading.cancel_order(db, user.account, order_id)
            except TradingError as exc:
                db.rollback()
                raise ToolError(exc.message)
        logger.info("MCP order cancelled by %s: %s", user.email, order.id)
        return OrderOut.model_validate(order).model_dump(mode="json")
    finally:
        db.close()


def _http_detail(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else str(exc)
