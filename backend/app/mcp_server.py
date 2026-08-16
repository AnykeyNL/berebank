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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .config import MIN_ORDER_EUR, PUBLIC_URL
from .database import SessionLocal
from .models import User
from .oauth import oauth_provider
from .routers.leaderboard import get_leaderboard as _get_leaderboard
from .routers.leaderboard import get_leaderboard_history as _get_leaderboard_history
from .routers.markets import get_analysis as _get_analysis
from .routers.markets import get_candles as _get_candles
from .routers.markets import get_fable5_analysis as _get_fable5_analysis
from .routers.markets import get_fable5_outlooks as _get_fable5_outlooks
from .routers.markets import get_gtp56sol_analysis as _get_gtp56sol_analysis
from .routers.markets import get_gtp56sol_outlooks as _get_gtp56sol_outlooks
from .routers.markets import get_kimi_analysis as _get_kimi_analysis
from .routers.markets import get_kimi_outlooks as _get_kimi_outlooks
from .routers.markets import get_market_hours as _get_market_hours
from .routers.markets import get_news as _get_news
from .routers.markets import get_opus_analysis as _get_opus_analysis
from .routers.markets import get_opus_outlooks as _get_opus_outlooks
from .routers.markets import get_opus_rankings as _get_opus_rankings
from .routers.markets import get_technical_outlooks as _get_technical_outlooks
from .routers.markets import list_markets as _list_markets
from .routers.orders import list_orders as _list_orders
from .routers.orders import list_trades as _list_trades
from .routers.orders import trade_history as _trade_history
from .routers.portfolio import get_portfolio as _get_portfolio
from .routers.portfolio import get_portfolio_history as _get_portfolio_history
from .schemas import OrderOut, PortfolioSnapshotOut
from .services import opus_calibration, trading
from .services.fees import get_30d_volume, get_fee_rates
from .services.market_data import market_data_service
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
        "the user to have enabled trading via MCP in their BereBank profile: call "
        "get_account_status to check that (plus the fee tier and minimum order size) "
        "before working out a trade plan, rather than discovering it on the first "
        "order. place_order takes a client_order_id that makes retries safe, a "
        "validate_only flag that prices an order without placing it, and an "
        "expiry (time_in_force day/gtd with expires_at or expires_in_sessions) "
        "counted in trading sessions from the exchange calendar rather than in "
        "wall-clock hours; get_market_hours says when a closed market opens "
        "again. To compare "
        "markets or engines, get_outlooks answers for many at once; the per-market "
        "analysis tools omit candles and indicator series unless verbose=true."
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


async def _opus_portfolio_advice(*, user: User, db: Session, horizon: str) -> dict:
    """Join the Opus ranking to the user's holdings and free cash.

    Read-only: it sizes suggestions against available cash and the EUR 5
    minimum order, but never places anything.
    """
    portfolio = _get_portfolio(user=user, db=db)
    rankings = await _get_opus_rankings(
        user=user, db=db, horizon=horizon, side="buy", limit=600
    )
    rows = {row["market"]: row for row in rankings["rankings"]}

    holdings = []
    held_markets: set[str] = set()
    for holding in portfolio.holdings:
        if holding.market is None:
            continue
        held_markets.add(holding.market)
        row = rows.get(holding.market)
        holdings.append({
            "market": holding.market,
            "asset": holding.asset,
            "amount": str(holding.amount + holding.reserved),
            "eur_value": None if holding.eur_value is None else str(holding.eur_value),
            "action": None if row is None else row["action"],
            "direction": None if row is None else row["direction"],
            "score": None if row is None else row["score"],
            "sell_score": None if row is None else row["sell_score"],
            "sell_rank": None if row is None else row["sell_rank"],
            "expected_return_pct": None if row is None else row["expected_return_pct"],
            "sell_edge_pct": None if row is None else row["sell_edge_pct"],
            "confidence": None if row is None else row["confidence"],
            "tradable_now": None if row is None else row["tradable_now"],
        })
    holdings.sort(key=lambda item: item["sell_rank"] or 10**6)

    candidates = [
        rows[market]
        for market in rankings["basket"]
        if market in rows and market not in held_markets
    ]
    weights = [max(0.0, float(row["buy_score"])) for row in candidates]
    total_weight = sum(weights)
    cash = float(portfolio.balance_eur)
    allocation = []
    for row, weight in zip(candidates, weights):
        amount = cash * weight / total_weight if total_weight > 0 else 0.0
        if amount < float(MIN_ORDER_EUR):
            continue
        allocation.append({
            "market": row["market"],
            "eur_amount": f"{amount:.2f}",
            "action": row["action"],
            "order_type": row["suggested_order_type"],
            "expected_return_pct": row["expected_return_pct"],
            "net_edge_pct": row["net_edge_pct"],
            "conviction": row["conviction"],
        })

    return {
        "generated_at": rankings["generated_at"],
        "horizon": horizon,
        "regimes": rankings["regimes"],
        "macro": rankings["macro"],
        "cash_eur": str(portfolio.balance_eur),
        "reserved_eur": str(portfolio.reserved_eur),
        "total_value_eur": str(portfolio.total_value_eur),
        "fee_tier": portfolio.fee_tier.model_dump(mode="json"),
        "holdings": holdings,
        "buy_candidates": candidates,
        "suggested_allocation": allocation,
        "minimum_order_eur": str(MIN_ORDER_EUR),
        "note": (
            "Suggestions only — nothing was ordered. Over short horizons Opus "
            "often finds no trade whose edge clears the fees, in which case the "
            "candidate list is empty on purpose."
        ),
    }


def _parse_decimal(value: str | float | int | None, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ToolError(f"Invalid decimal value for {field}: {value!r}")


def _parse_datetime(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(
            f"Invalid ISO 8601 timestamp for {field}: {value!r} "
            '(expected something like "2026-08-20T16:00:00Z")'
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@mcp.tool()
def list_markets(filter: str | None = None, asset_class: str | None = None) -> list[dict]:
    """List EUR markets with live prices (last/bid/ask), 24h change and volume.

    Markets cover crypto plus US stocks, funds and commodities (gold, silver,
    platinum, palladium and oil); each row has an asset_class of "crypto",
    "stock", "fund" or "commodity" (non-crypto rows also carry a market_open
    flag). Optionally filter by asset_class and/or by a case-insensitive
    substring of the market symbol, e.g. "BTC" matches BTC-EUR. Prices are
    EUR decimals as strings.

    Every row also carries its order sizing rules, so an order never has to be
    guessed at. `min_order_eur` is the smallest order value and
    `amount_quantum` the smallest amount step, both enforced by this engine on
    every market. `tick_size`, `amount_decimals` and `min_order_base` are
    Bitvavo's own rules for that market and are null for stocks, funds and
    commodities, which come from Twelve Data and carry no venue precision. To
    check one specific order before committing to it, call `place_order` with
    validate_only=true.
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
def get_market_hours(market: str | None = None, asset_class: str | None = None) -> dict:
    """When a market is open, and when it next opens or closes.

    Trading hours as data rather than a snapshot, so waiting time can be
    computed instead of guessed. Pass a `market` for one instrument, an
    `asset_class` for a whole class, or neither to get all four at once.

    Each entry has `is_open` (the answer to act on: the live feed where there
    is one, since that also catches halts, and the trading calendar
    otherwise), `next_open`, `next_close` and `current_session_end` as UTC
    ISO 8601 timestamps, plus the calendar and its timezone. Crypto reports
    `always_open` with null timestamps. `calendar_open` and `live_market_open`
    are also given separately, so a halt or an unscheduled closure is visible
    rather than hidden behind one flag.

    Stock and fund hours come from the NYSE calendar and commodities from the
    24/5 forex calendar, both including holidays and early closes — the day
    after Thanksgiving really does close at 13:00 ET. Use this before placing a
    market order on a closed exchange, or to decide how long a limit order will
    have to rest.
    """
    if asset_class is not None and asset_class not in ("crypto", "stock", "fund", "commodity"):
        raise ToolError('asset_class must be "crypto", "stock", "fund" or "commodity"')
    db = SessionLocal()
    try:
        user = _current_user(db)
    finally:
        db.close()
    try:
        return _get_market_hours(user=user, market=market, asset_class=asset_class)
    except Exception as exc:
        raise ToolError(_http_detail(exc))


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
async def analyze_market(market: str, range: str = "30d", verbose: bool = False) -> dict:
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
    explanation of how the strategy works and key values (decimal strings).

    Set `verbose` to true to also get the chart payload: the candles of the
    display window plus an overlay series per indicator. It defaults to false
    because those arrays dominate the response and are rarely needed to reach
    a conclusion. Signals are educational indications from a paper-money
    simulation, not financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
    finally:
        db.close()
    try:
        return await _get_analysis(market, user=user, range_=range, verbose=verbose)
    except Exception as exc:
        raise ToolError(_http_detail(exc))


@mcp.tool()
async def get_kimi_analysis(market: str, range: str = "30d", verbose: bool = False) -> dict:
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
    forward returns).

    Set `verbose` to true to also get the chart payload: the candles of the
    display window plus an overlay series per indicator. It defaults to false
    because those arrays dominate the response and are rarely needed to reach
    a conclusion. To compare several markets or engines, use `get_outlooks`,
    which returns just the verdicts in one call. Educational indication from a
    paper-money simulation, not financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_kimi_analysis(
                market, user=user, db=db, range_=range, verbose=verbose
            )
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_fable5_analysis(market: str, range: str = "30d", verbose: bool = False) -> dict:
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
    returns).

    Set `verbose` to true to also get the chart payload: the candles of the
    display window plus an overlay series per indicator. It defaults to false
    because those arrays dominate the response and are rarely needed to reach
    a conclusion. To compare several markets or engines, use `get_outlooks`,
    which returns just the verdicts in one call. Educational indication from a
    paper-money simulation, not financial advice.
    """
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_fable5_analysis(
                market, user=user, db=db, range_=range, verbose=verbose
            )
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


_OUTLOOK_ENGINES = ("technical", "kimi", "fable5", "opus", "gtp56sol")
# GTP56Sol names the 21-bar window "1m" where Opus calls it "4w".
_GTP56SOL_HORIZON = {"1d": "1d", "1w": "1w", "4w": "1m"}
_OUTLOOKS_MAX_MARKETS = 500


async def _engine_outlooks(engine: str, *, user: User, db: Session, horizon: str) -> dict:
    """One engine's whole-universe outlook payload, from its shared TTL cache."""
    if engine == "technical":
        return await run_in_threadpool(_get_technical_outlooks, user=user, db=db)
    if engine == "kimi":
        return await _get_kimi_outlooks(user=user, db=db)
    if engine == "fable5":
        return await _get_fable5_outlooks(user=user, db=db)
    if engine == "opus":
        return await _get_opus_outlooks(user=user, db=db, horizon=horizon)
    return await run_in_threadpool(
        _get_gtp56sol_outlooks, user=user, db=db, horizon=_GTP56SOL_HORIZON[horizon]
    )


@mcp.tool()
async def get_outlooks(
    markets: list[str] | None = None,
    engines: list[str] | None = None,
    asset_class: str | None = None,
    horizon: str = "1w",
    limit: int = 100,
) -> dict:
    """Direction outlooks for many markets and engines in a single call.

    This is the tool for screening and for consensus checks: instead of one
    `get_kimi_analysis` plus one `get_fable5_analysis` per market, ask for the
    markets and engines you care about at once. Only the verdict is returned —
    direction, score, buy/sell scores, confidence, regime — with no candles and
    no indicator series, so a four-market cross-engine check costs one small
    response instead of a dozen large ones.

    Arguments:
        markets: Market symbols, e.g. ["BTC-EUR", "AAPL-EUR"]. When omitted,
            `asset_class` is required and the whole class is returned.
        engines: Any of "technical", "kimi", "fable5", "opus", "gtp56sol".
            Defaults to all five.
        asset_class: Narrow to "crypto", "stock", "fund" or "commodity".
        horizon: "1d", "1w" (default) or "4w"; only affects opus and gtp56sol.
        limit: Cap on the number of markets returned when `markets` is omitted
            (1..500, default 100, alphabetical); ignored when `markets` is given.

    Outlooks are computed from the stored daily candles that are harvested in
    the background, so a market only appears once it has at least 60 days of
    history, and the numbers are the daily-bar view of each engine. The
    per-market tools (`get_kimi_analysis`, `get_fable5_analysis`,
    `get_opus_analysis`, `get_gtp56sol_analysis`) score the range you ask for —
    4-hour bars for crypto on "30d", for instance — so their scores can differ
    slightly. Use this tool to pick a shortlist, then the per-market tool to
    understand why. Each engine's pass is shared and cached for 15 minutes; the
    first call after a cache miss scores every market and can take a while.

    Markets you asked for that no engine could score are listed separately, so
    an absent market is never silently dropped. Educational indications from a
    paper-money simulation, not financial advice.
    """
    if asset_class is not None and asset_class not in ("crypto", "stock", "fund", "commodity"):
        raise ToolError('asset_class must be "crypto", "stock", "fund" or "commodity"')
    if horizon not in opus_calibration.HORIZONS:
        raise ToolError(f'horizon must be one of {", ".join(opus_calibration.HORIZONS)}')
    if limit < 1 or limit > _OUTLOOKS_MAX_MARKETS:
        raise ToolError(f"limit must be between 1 and {_OUTLOOKS_MAX_MARKETS}")

    selected: list[str] = []
    for engine in engines if engines else _OUTLOOK_ENGINES:
        if engine not in _OUTLOOK_ENGINES:
            raise ToolError(
                f"unknown engine {engine!r}; use one of {', '.join(_OUTLOOK_ENGINES)}"
            )
        if engine not in selected:
            selected.append(engine)

    wanted: set[str] | None = None
    unknown: list[str] = []
    if markets:
        wanted = set()
        for symbol in markets:
            symbol = symbol.upper()
            if market_data_service.get_market(symbol) is None:
                unknown.append(symbol)
            else:
                wanted.add(symbol)
        if not wanted:
            raise ToolError(f"unknown market(s): {', '.join(unknown)}")
    elif asset_class is None:
        raise ToolError(
            "pass markets, or an asset_class to cover a whole class; "
            "use get_opus_rankings to screen every market at once"
        )

    def in_scope(market: str) -> bool:
        if wanted is not None and market not in wanted:
            return False
        if asset_class:
            info = market_data_service.get_market(market)
            if info is None or info["asset_class"] != asset_class:
                return False
        return True

    db = SessionLocal()
    try:
        user = _current_user(db)
        outlooks: dict[str, dict] = {}
        meta: dict[str, dict] = {}
        for engine in selected:
            try:
                payload = await _engine_outlooks(engine, user=user, db=db, horizon=horizon)
            except Exception as exc:
                raise ToolError(_http_detail(exc))
            rows = payload.get("outlooks") or {}
            meta[engine] = {
                "generated_at": payload.get("generated_at"),
                "scored_markets": len(rows),
            }
            for market, outlook in rows.items():
                if in_scope(market):
                    outlooks.setdefault(market, {})[engine] = outlook
    finally:
        db.close()

    truncated = False
    if wanted is None and len(outlooks) > limit:
        truncated = True
        outlooks = {market: outlooks[market] for market in sorted(outlooks)[:limit]}

    result: dict = {
        "horizon": horizon,
        "engines": meta,
        "outlooks": outlooks,
        "truncated": truncated,
    }
    if unknown:
        result["unknown_markets"] = unknown
    if wanted is not None:
        no_history = sorted(wanted - set(outlooks))
        if no_history:
            result["without_outlook"] = no_history
            result["without_outlook_reason"] = (
                "no engine has scored these markets yet; they need at least 60 "
                "days of harvested daily candles"
            )
    return result


@mcp.tool()
async def get_opus_rankings(
    horizon: str = "1w",
    asset_class: str | None = None,
    side: str = "buy",
    limit: int = 20,
) -> dict:
    """Opus buy/sell ranking of every market for a 1-day to 4-week horizon.

    This is the tool to reach for when the question is "what should I buy or
    sell right now?". Where the other engines score one market against absolute
    thresholds, Opus ranks every crypto, stock, fund and commodity against its
    peers *on the same day* and expresses the answer in euros after fees.

    How a row is produced:

    - Each of ~19 features (volatility-adjusted momentum over two horizons,
      short-term reversal, distance to the 50-day mean in ATR units, signed ADX,
      RSI, Bollinger and 20-day range position, volatility expansion and level,
      drawdown, volume surge, turnover, beta/correlation/residual momentum
      versus the peer index, sensitivity to VIX, the 10-year yield, crypto
      sentiment and stablecoin supply, and perpetual funding) is rank-scored
      within the market's peer group for that day.
    - Weights are learned, not chosen: they are the shrunk walk-forward
      information coefficient of each feature against forward returns over the
      stored daily history, estimated per peer group, per horizon and per market
      regime. A feature whose predictive sign is statistically indistinguishable
      from noise gets weight zero.
    - The weighted composite is mapped through a calibrated score-to-return
      table into `expected_return_pct`, plus the peer group's drift scaled by
      this market's beta.
    - `net_edge_pct` subtracts the real round-trip Bitvavo fee for the calling
      user's own tier; `net_edge_limit_pct` does the same with maker fees, and
      `suggested_order_type` becomes "limit" when only the maker path is
      profitable. Over a 1-day horizon most edges do not clear a taker round
      trip at all — Opus will say so rather than invent a trade.
    - `buy_score` and `sell_score` (0..100) are the fee-aware edge divided by
      the expected move over the horizon, i.e. conviction per unit of risk.
      `action` is one of strong_buy, buy, hold, reduce, sell.

    Arguments: `horizon` is "1d", "1w" (default) or "4w"; `asset_class`
    optionally narrows to crypto, stock, fund or commodity; `side` is "buy" or
    "sell" and decides the ordering; `limit` is 1..200 rows.

    The response also carries `basket` — a diversified shortlist of the best
    buys, capped per peer group so it is not the same bet ten times — plus the
    current `regimes`, the `macro` backdrop (VIX, yield curve, Fear & Greed,
    stablecoin growth) and whether the engine is `calibrated` yet. Rows flagged
    `stale`, `low_volatility` or not `liquidity_ok` are ranked last and never
    recommended as buys. Educational indication from a paper-money simulation,
    not financial advice.
    """
    if horizon not in opus_calibration.HORIZONS:
        raise ToolError(f'horizon must be one of {", ".join(opus_calibration.HORIZONS)}')
    if side not in ("buy", "sell"):
        raise ToolError('side must be "buy" or "sell"')
    if asset_class is not None and asset_class not in ("crypto", "stock", "fund", "commodity"):
        raise ToolError('asset_class must be "crypto", "stock", "fund" or "commodity"')
    if limit < 1 or limit > 200:
        raise ToolError("limit must be between 1 and 200")
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_opus_rankings(
                user=user,
                db=db,
                horizon=horizon,
                asset_class=asset_class,
                side=side,
                limit=limit,
            )
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_opus_analysis(
    market: str, range: str = "30d", horizon: str = "1w", verbose: bool = False
) -> dict:
    """Opus recommendation for one market, with the full feature breakdown.

    Same engine as `get_opus_rankings`, zoomed in on a single market. Returns
    the verdict (direction, score -100..+100, buy/sell scores, confidence and
    regime), the `recommendation` block (expected return, the fees it must
    clear, net edge for taker and maker orders, conviction, action and a stop
    loss two ATRs below price), and every feature with its peer percentile,
    learned weight, information coefficient and contribution — so the answer
    can always be explained rather than asserted.

    `cross_section` says which peer group the market was ranked in, how many
    peers it was compared with and which completed day the features come from.
    `gates` reports tradability: liquidity, data freshness, market hours and
    whether the instrument moves enough to be worth a fee at all.
    `calibration` is the provenance of the weights (peer group, horizon,
    regime, sample days, walk-forward information coefficient and hit rate).

    Two track records are included: `track_record` replays this market's own
    history through the engine, while `live_track_record` reports how the
    recommendations Opus actually published for it performed, and
    `live_track_record_all` does the same across every market.

    Range is "1d", "1w", "30d" (default), "90d", "180d" or "365d" and only
    affects the returned candles and display window. Horizon is "1d", "1w"
    (default) or "4w" and selects the calibration. Set `verbose` to true to
    include the candles of the display window; it defaults to false because
    the feature table, not the chart, carries the answer. Educational
    indication from a paper-money simulation, not financial advice.
    """
    if horizon not in opus_calibration.HORIZONS:
        raise ToolError(f'horizon must be one of {", ".join(opus_calibration.HORIZONS)}')
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _get_opus_analysis(
                market, user=user, db=db, range_=range, horizon=horizon, verbose=verbose
            )
        except Exception as exc:
            raise ToolError(_http_detail(exc))
    finally:
        db.close()


@mcp.tool()
async def get_opus_portfolio_advice(horizon: str = "1w") -> dict:
    """Join the Opus ranking to the user's actual portfolio. Read-only.

    Answers "what should I do with what I hold, and where should my cash go?"
    in one call:

    - `holdings` lists every position with its Opus verdict, the exit edge after
      the sell fee, the action, and its rank on the sell board — so the weakest
      holdings are obvious.
    - `buy_candidates` is the diversified basket of best buys the user does not
      already hold, each with expected return, net edge after fees, conviction
      and the order type that makes the edge work.
    - `suggested_allocation` splits the available EUR cash over those candidates
      in proportion to conviction, respecting the EUR 5 minimum order size and
      leaving the fee out of the invested amount. It is a suggestion only:
      nothing is ordered, and placing orders still requires `place_order` and
      the user's explicit MCP trading permission.

    Horizon is "1d", "1w" (default) or "4w". Note that over short horizons Opus
    often finds no trade whose edge clears the fees; an empty candidate list is
    a real answer, not a failure. Educational indication from a paper-money
    simulation, not financial advice.
    """
    if horizon not in opus_calibration.HORIZONS:
        raise ToolError(f'horizon must be one of {", ".join(opus_calibration.HORIZONS)}')
    db = SessionLocal()
    try:
        user = _current_user(db)
        try:
            return await _opus_portfolio_advice(user=user, db=db, horizon=horizon)
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


_TRADING_TOOLS = ("place_order", "cancel_order")


@mcp.tool()
def get_account_status() -> dict:
    """What this connection is allowed to do, before you plan around it.

    Call this first when a session may end in trading. It reports whether the
    user has enabled "Allow trading via MCP" in their BereBank profile, which
    tools that unlocks, the granted OAuth scopes, the account's current
    maker/taker fee tier with the 30-day volume behind it, the EUR 5 minimum
    order value and the server's UTC time. When trading is disabled, every
    read tool still works — say so before drawing up a trade plan that cannot
    be executed. The setting is checked again on every order, so a user can
    turn it on without reconnecting.
    """
    token = get_access_token()
    db = SessionLocal()
    try:
        user = _current_user(db)
        volume = get_30d_volume(db, user.account.id)
        maker, taker = get_fee_rates(volume)
        return {
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "is_active": user.is_active,
            "trading_enabled": user.mcp_trading_enabled,
            "trading_tools": list(_TRADING_TOOLS) if user.mcp_trading_enabled else [],
            "trading_disabled_reason": None if user.mcp_trading_enabled else (
                "The user has not enabled 'Allow trading via MCP' in their BereBank "
                "profile (MCP access section). Read tools are unaffected."
            ),
            "scopes": list(token.scopes) if token is not None else [],
            "fee_tier": {
                "volume_30d_eur": str(volume),
                "maker_pct": str(maker * 100),
                "taker_pct": str(taker * 100),
            },
            "minimum_order_eur": str(MIN_ORDER_EUR),
            "server_time_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    finally:
        db.close()


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

    Optionally filter by status: "open", "filled", "cancelled" or "expired"
    (a resting order that reached its time_in_force; its reservation was
    released just as a cancellation would). Each order carries the
    `client_order_id` it was placed under, plus `time_in_force` and the
    resolved `expires_at`, so state can be reconstructed after a restart.
    """
    if status is not None and status not in ("open", "filled", "cancelled", "expired"):
        raise ToolError('status must be "open", "filled", "cancelled" or "expired"')
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
def get_leaderboard_history(days: int = 30, interval: str = "day") -> dict:
    """How the user's rank moved over the past `days` (1-180, default 30).

    Where `get_leaderboard` is a photograph, this is the film: one point per
    `interval` ("day", default, or "hour"), each with the user's `rank` and
    `total_eur`, the `leader_total_eur` to measure the gap, and how many
    `traders` were ranked. Points come from the hourly account-value
    snapshots, so a day is its last recorded value and history starts when
    the account did. The snapshots hold only the total: for the cash/assets
    split or trade counts, call `get_leaderboard`.
    """
    if days < 1 or days > 180:
        raise ToolError("days must be between 1 and 180")
    if interval not in ("hour", "day"):
        raise ToolError('interval must be "hour" or "day"')
    db = SessionLocal()
    try:
        user = _current_user(db)
        history = _get_leaderboard_history(
            days=days, interval=interval, user=user, db=db
        )
        return history.model_dump(mode="json")
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
    trigger_price: str | None = None,
    client_order_id: str | None = None,
    validate_only: bool = False,
    time_in_force: str | None = None,
    expires_at: str | None = None,
    expires_in_sessions: int | None = None,
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
        client_order_id: Your own id for this order (max 64 characters), which
            makes the call safe to retry. If the response to a placement is
            lost, replaying the exact same call returns the order that was
            already stored under this id, flagged with duplicate=true, instead
            of placing a second one. Ids are unique per account and are
            returned on every order, so `list_orders` can be used to recover
            state after a crash.
        validate_only: Run every check and report what the order would cost
            without placing it. The response is a preview with the price,
            amount, fee and resulting balance the engine would use, so order
            size, decimal precision and affordability can be verified up front.
        time_in_force: How long a resting order stays alive: "gtc" (default,
            until filled or cancelled), "day" (until the end of the current or
            next trading session) or "gtd" (until a moment you set). Not
            applicable to market orders, which fill or fail immediately.
        expires_at: UTC ISO 8601 moment for a "gtd" order, e.g.
            "2026-08-20T16:00:00Z".
        expires_in_sessions: Alternative to expires_at, counted in **trading
            sessions** rather than hours — the only reading that survives a
            weekend. A NYSE order placed on Saturday with 2 expires at
            Tuesday's close, not forty hours later. For crypto, which never
            closes, a session is a 24-hour day. Use `get_market_hours` to see
            when sessions start and end.

    Both `expires_at` and the resolved `time_in_force` come back on the order,
    so you can see exactly what was agreed rather than having to infer it.
    Expired orders get status "expired" and release their reservation, the same
    way a cancellation does.

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
        args = (
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
        expiry = {
            "time_in_force": time_in_force,
            "expires_at": _parse_datetime(expires_at, "expires_at"),
            "expires_in_sessions": expires_in_sessions,
        }
        if validate_only:
            try:
                return trading.preview_order(*args, **expiry)
            except TradingError as exc:
                db.rollback()
                raise ToolError(exc.message)
        async with trade_lock:
            try:
                key = trading.normalize_client_order_id(client_order_id)
                duplicate = key is not None and trading.find_client_order(
                    db, user.account, key
                ) is not None
                order = trading.place_order(*args, client_order_id=key, **expiry)
            except TradingError as exc:
                db.rollback()
                raise ToolError(exc.message)
        if duplicate:
            logger.info("MCP order replay by %s: %s", user.email, order.id)
        else:
            logger.info("MCP order placed by %s: %s", user.email, order.id)
        return {
            **OrderOut.model_validate(order).model_dump(mode="json"),
            "duplicate": duplicate,
        }
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
