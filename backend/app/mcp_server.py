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
from .routers.leaderboard import get_leaderboard as _get_leaderboard
from .routers.markets import get_analysis as _get_analysis
from .routers.markets import get_candles as _get_candles
from .routers.markets import get_fable5_analysis as _get_fable5_analysis
from .routers.markets import get_gtp56sol_analysis as _get_gtp56sol_analysis
from .routers.markets import get_kimi_analysis as _get_kimi_analysis
from .routers.markets import get_news as _get_news
from .routers.markets import list_markets as _list_markets
from .routers.orders import list_orders as _list_orders
from .routers.orders import list_trades as _list_trades
from .routers.orders import trade_history as _trade_history
from .routers.portfolio import get_portfolio as _get_portfolio
from .routers.portfolio import get_portfolio_history as _get_portfolio_history
from .schemas import OrderOut, PortfolioSnapshotOut
from .services import trading
from .services.trading import TradingError, trade_lock

logger = logging.getLogger("berebank.mcp")

mcp = FastMCP(
    "de BereBank",
    instructions=(
        "de BereBank is a simulated exchange: users trade with paper money in EUR "
        "against live market data (crypto via Bitvavo; US stocks, funds and "
        "commodities via Twelve Data), with realistic maker/taker fees. Amounts and "
        "prices are decimal numbers serialized as strings. Market orders on "
        "stocks, funds and commodities are rejected while the market is closed. "
        "Besides market and limit orders, stop-loss "
        "sell orders are supported: they trigger when the price falls to the trigger "
        "price and then sell at the live bid. Placing or cancelling orders requires "
        "the user to have enabled trading via MCP in their BereBank profile."
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

    Markets cover crypto plus US stocks, funds and commodities (gold, silver,
    platinum, palladium and oil); each row has an asset_class of "crypto",
    "stock", "fund" or "commodity" (non-crypto rows also carry a market_open
    flag). Optionally filter by asset_class and/or by a case-insensitive
    substring of the market symbol, e.g. "BTC" matches BTC-EUR. Prices are
    EUR decimals as strings.
    """
    if asset_class is not None and asset_class not in ("crypto", "stock", "fund", "commodity"):
        raise ToolError('asset_class must be "crypto", "stock", "fund" or "commodity"')
    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _list_markets(user=user, db=db, asset_class=asset_class)
    finally:
        db.close()
    if filter:
        needle = filter.upper()
        rows = [m for m in rows if needle in m.market]
    return [m.model_dump(mode="json") for m in rows]


@mcp.tool()
async def get_candles(market: str, range: str = "1d") -> list[list]:
    """Get OHLCV candles for a market (e.g. BTC-EUR) over a past range.

    Returns a list of [timestamp_ms, open, high, low, close, volume], oldest
    first. Range is one of "1h", "1d", "1w", "30d", "90d", "180d" or "365d"
    (default "1d"); the bar interval scales with the range, from 1-minute
    bars for "1h" up to daily bars for "90d" and longer. Stocks and funds
    only have bars during exchange hours; commodities follow forex hours
    (roughly 24/5).
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
    finally:
        db.close()
    try:
        return await _get_candles(market, user=user, range_=range)
    except Exception as exc:
        raise ToolError(_http_detail(exc))


@mcp.tool()
async def analyze_market(market: str, range: str = "30d") -> dict:
    """Run technical analysis on a market (e.g. BTC-EUR) over a past range.

    Range is one of "1d", "1w", "30d", "90d", "180d" or "365d" (default
    "30d"). Five strategies are computed from OHLCV candles, identically to
    the web app's Analyze page:

    - trend: SMA-20/50 and EMA-12/26 moving averages, golden/death crosses
    - rsi: RSI-14 overbought/oversold momentum
    - macd: MACD (12, 26, 9) signal-line crossovers and histogram
    - volatility: Bollinger Bands (20, 2 sigma) and ATR-14 (includes a
      suggested stop-loss price two ATRs below the current price)
    - levels_volume: clustered support/resistance levels plus volume trend

    Each strategy returns a signal ("bullish", "bearish", "neutral", or
    "none" when there is not enough data), a structured reason, an
    explanation of how the strategy works, key values (decimal strings) and
    indicator series. The response also includes the candles of the display
    window. Signals are educational indications from a paper-money
    simulation, not financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
    finally:
        db.close()
    try:
        return await _get_analysis(market, user=user, range_=range)
    except Exception as exc:
        raise ToolError(_http_detail(exc))


@mcp.tool()
async def get_kimi_analysis(market: str, range: str = "30d") -> dict:
    """KimiK3 direction outlook for a market (e.g. BTC-EUR): a single
    bullish/bearish/neutral verdict blended from eight price strategies
    (trend, RSI, MACD, Bollinger volatility, support/resistance with
    volume, ADX trend strength, dual-horizon momentum and a slow
    stochastic) plus asset-class context signals — for crypto: Fear &
    Greed sentiment momentum, BTC-aware dominance/stablecoin liquidity,
    Coinglass funding level and 4h funding momentum, price-confirmed open
    interest on 1h/4h/24h windows, long/short positioning and liquidation
    flows; for stocks: VIX, yield curve, sector relative strength,
    earnings-proximity brake and insider flow; for funds/commodities:
    safe-haven aware VIX/yield logic (gold, Treasuries, precious metals;
    omitted for energy) and crypto macro signals for IBIT.

    Range is one of "1d", "1w", "30d", "90d", "180d" or "365d" (default
    "30d"). The outlook contains a direction, a score from -100 (strongly
    bearish) to +100 (strongly bullish), a buy_score and sell_score (0-100
    shares of active regime-weighted weight voting bullish resp. bearish;
    higher buy_score = more evidence favors buying, high on both sides =
    contested market), a confidence level (low/medium/high, based on how
    many strategies agree), the market regime (trending/ranging) used for
    weighting, and per-strategy contributions showing each vote. When
    enough daily history has been harvested, a track_record shows how
    often past outlooks on this market were followed by a move in the
    indicated direction within 5 days (hit_rate_pct, samples, average
    forward returns). Educational indication from a paper-money
    simulation, not financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_kimi_analysis(market, user=user, db=db, range_=range)
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_fable5_analysis(market: str, range: str = "30d") -> dict:
    """Fable5 direction outlook for a market (e.g. BTC-EUR): a single
    bullish/bearish/neutral verdict blended from eight technical-analysis
    signals (trend, MACD, dual-horizon momentum, ADX trend strength, RSI,
    slow stochastic, Bollinger volatility, and support/resistance with
    volume) plus asset-class specific context signals, all with fixed
    importance weights. Crypto adds Fear & Greed sentiment momentum,
    BTC-dominance/stablecoin liquidity, Coinglass funding, price-confirmed
    open-interest momentum, cross-exchange long/short positioning and 24h
    liquidation flows. Stocks add VIX level/change, the treasury yield
    curve, 20-day sector relative strength and an earnings-proximity brake;
    funds get the macro signals; precious-metal commodities read VIX and
    the yield curve as safe-haven signals.

    Range is one of "1d", "1w", "30d", "90d", "180d" or "365d" (default
    "30d"). The outlook contains a direction, a score from -100 (strongly
    bearish) to +100 (strongly bullish) rendered as a five-zone gauge in the
    web app, a buy_score and sell_score (0..100 shares of active signal
    weight voting bullish resp. bearish — higher buy_score means more of the
    weighted evidence favors buying; high values on both sides mean the
    market is contested), a confidence level (low/medium/high, from the weighted share of
    signals agreeing with the verdict), the ADX market regime
    (trending/ranging, context only — weights never change), and
    per-strategy contributions showing each vote and weight. When enough
    daily history has been harvested, a track_record shows how often past
    outlooks on this market were followed by a move in the indicated
    direction within 5 days (hit_rate_pct, samples, average forward
    returns). Educational indication from a paper-money simulation, not
    financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_fable5_analysis(market, user=user, db=db, range_=range)
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_gtp56sol_analysis(market: str, horizon: str = "1w") -> dict:
    """Explainable historical-pattern probabilities for a market.

    Horizon is "1d", "1w" (default), or "1m", meaning respectively 1, 5,
    or 21 forward trading-session bars rather than calendar days. The result
    reports Up/Sideways/Down probabilities, similar historical evidence,
    walk-forward validation, direction, and conservative confidence. When
    the requested asset lacks enough history, evidence may include a bounded
    set of other markets from the same asset class only. This is an
    educational indication in a paper-money simulation, not financial advice
    or a guarantee of future results.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_gtp56sol_analysis(
                market, user=user, db=db, horizon=horizon
            )
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_news(market: str, limit: int = 10) -> list[dict]:
    """Recent news for a market (e.g. BTC-EUR, AAPL-EUR, SPY-EUR).

    Returns a list of items with id, datetime, title, body, language codes,
    and optional url/source fields, newest first. Combines RSS-matched articles
    with Twelve Data press releases for stocks and funds. Limit is 1–10.
    """
    if limit < 1 or limit > 10:
        raise ToolError("limit must be between 1 and 10")
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            rows = await _get_news(market, user=user, db=db, limit=limit)
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()
    return [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in rows]


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
def get_portfolio_history() -> list[dict]:
    """Get the user's portfolio value history over the past 30 days.

    Returns hourly snapshots, oldest first, each with created_at,
    total_value_eur (cash + reserved funds + holdings at the live price,
    as a decimal string) and asset_count (distinct assets held, including
    assets locked in open sell orders). Recording starts when the account
    becomes active on the platform, so new accounts may have less than 30
    days of history. Useful for charting performance over time or comparing
    against market benchmarks.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        rows = _get_portfolio_history(user=user, db=db)
        return [PortfolioSnapshotOut.model_validate(r).model_dump(mode="json") for r in rows]
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
def get_leaderboard() -> list[dict]:
    """Get the competition leaderboard: all active traders ranked by total
    account value, highest first.

    Each entry has rank, display_name, trades (executed trade count),
    cash_eur (EUR balance plus funds reserved for open limit buys),
    assets_eur (holdings valued at the live last price) and total_eur
    (cash + assets — the score that decides the competition). The entry
    belonging to the connected user is marked with is_you=true.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        entries = _get_leaderboard(user=user, db=db)
        user_id = user.id
    finally:
        db.close()
    result = []
    for rank, entry in enumerate(entries, start=1):
        row = entry.model_dump(mode="json")
        row["rank"] = rank
        row["is_you"] = entry.user_id == user_id
        del row["user_id"]
        result.append(row)
    return result


@mcp.tool()
async def place_order(
    market: str,
    side: str,
    order_type: str,
    amount: str | None = None,
    amount_quote: str | None = None,
    limit_price: str | None = None,
    trigger_price: str | None = None,
) -> dict:
    """Place an order. Requires trading via MCP to be enabled in the user's profile.

    Args:
        market: Market symbol, e.g. "BTC-EUR".
        side: "buy" or "sell".
        order_type: "market" (fills immediately at live price, taker fee),
            "limit" (fills when the price crosses limit_price, maker fee), or
            "stop_loss" (sell only: rests until the live bid drops to
            trigger_price, then sells at the live bid, taker fee; the fill can
            be below the trigger on a price gap).
        amount: Amount of the base asset (crypto), as a decimal string.
        amount_quote: EUR amount to spend/receive; market orders only.
            Market orders take exactly one of amount or amount_quote.
        limit_price: Limit price in EUR; required for limit orders (together
            with amount).
        trigger_price: Stop price in EUR; required for stop_loss orders
            (together with amount) and must be below the current price. The
            asset amount is reserved while the stop-loss rests; cancel via
            cancel_order to release it.

    Fees are charged in EUR. Minimum order value is EUR 5.
    """
    if side not in ("buy", "sell"):
        raise ToolError('side must be "buy" or "sell"')
    if order_type not in ("market", "limit", "stop_loss"):
        raise ToolError('order_type must be "market", "limit" or "stop_loss"')
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
                    _parse_decimal(trigger_price, "trigger_price"),
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
