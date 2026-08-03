from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, Money


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | bank_manager
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    preferred_language: Mapped[str | None] = mapped_column(String(5), nullable=True)  # en | nl
    mcp_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped["Account"] = relationship(back_populates="user", uselist=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    balance_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    user: Mapped[User] = relationship(back_populates="account")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="account")


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("account_id", "asset"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    asset: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    account: Mapped[Account] = relationship(back_populates="holdings")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)  # e.g. BTC-EUR
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    order_type: Mapped[str] = mapped_column(String(10))  # market | limit | stop_loss
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | filled | cancelled
    amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # base asset amount
    amount_quote: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # EUR amount (market orders)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    trigger_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # stop-loss trigger
    reserved_eur: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # for open limit buys
    fee_paid: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    filled_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    market: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))
    amount: Mapped[Decimal] = mapped_column(Money)
    price: Mapped[Decimal] = mapped_column(Money)
    eur_value: Mapped[Decimal] = mapped_column(Money)  # amount * price, excl. fee
    fee_eur: Mapped[Decimal] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PortfolioSnapshot(Base):
    """Hourly record of an account's total value, used for the portfolio chart."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    total_value_eur: Mapped[Decimal] = mapped_column(Money)
    asset_count: Mapped[int] = mapped_column(default=0)  # distinct assets held (incl. open sell orders)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MarketCandle(Base):
    """Daily OHLCV candle per market, harvested for the KimiK3 track record."""

    __tablename__ = "market_candles"
    __table_args__ = (UniqueConstraint("market", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC day start
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))


class OpusMacroSeries(Base):
    """One daily value of a named macro/derived series used by the Opus engine.

    Deliberately narrow (series id + day + value) so any number of external or
    in-house series share one table: FRED yields, Fear & Greed, stablecoin
    supply, per-coin funding, asset-class breadth, index levels. Keeps the
    dev/prod dataset transfer format trivial.
    """

    __tablename__ = "opus_macro_series"
    __table_args__ = (UniqueConstraint("series_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[str] = mapped_column(String(60), index=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC day start
    value: Mapped[float] = mapped_column(Float)


class OpusCalibration(Base):
    """Learned Opus feature weights for one peer group, horizon and regime.

    ``payload`` holds the JSON weight vector, the composite-to-return map and
    the information-coefficient diagnostics produced by the nightly
    walk-forward calibration.
    """

    __tablename__ = "opus_calibration"
    __table_args__ = (
        UniqueConstraint("engine_version", "peer_group", "horizon", "regime"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    engine_version: Mapped[str] = mapped_column(String(20), index=True)
    peer_group: Mapped[str] = mapped_column(String(20))  # crypto | stock | other
    horizon: Mapped[str] = mapped_column(String(4))      # 1d | 1w | 4w
    regime: Mapped[str] = mapped_column(String(10))      # all | up | down
    payload: Mapped[str] = mapped_column(Text)           # JSON
    samples: Mapped[int] = mapped_column(default=0)
    calibrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OpusRecommendation(Base):
    """Daily snapshot of one Opus recommendation, scored against reality later.

    Powers the live track record: the realized forward return is filled in once
    the horizon has passed, so the engine reports how its own published
    recommendations actually performed.
    """

    __tablename__ = "opus_recommendations"
    __table_args__ = (UniqueConstraint("day", "market", "horizon"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    horizon: Mapped[str] = mapped_column(String(4))
    action: Mapped[str] = mapped_column(String(12))      # strong_buy … sell
    direction: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    buy_score: Mapped[float] = mapped_column(Float, default=0.0)
    sell_score: Mapped[float] = mapped_column(Float, default=0.0)
    expected_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_edge_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    conviction: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_rank: Mapped[int | None] = mapped_column(nullable=True)
    close_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class OAuthClient(Base):
    """Dynamically registered OAuth client (an MCP client application)."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_info: Mapped[str] = mapped_column(Text)  # OAuthClientInformationFull as JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthAuthCode(Base):
    """Short-lived authorization code issued after login/consent."""

    __tablename__ = "oauth_auth_codes"

    code: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    scopes: Mapped[str] = mapped_column(Text, default="")  # space-separated
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(Text)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(Boolean, default=True)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[float] = mapped_column(Float)  # epoch seconds


class OAuthRefreshToken(Base):
    """Opaque, revocable refresh token for the MCP OAuth flow."""

    __tablename__ = "oauth_refresh_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    scopes: Mapped[str] = mapped_column(Text, default="")  # space-separated
    expires_at: Mapped[float] = mapped_column(Float)  # epoch seconds
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RssFeed(Base):
    __tablename__ = "rss_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    articles: Mapped[list["NewsArticle"]] = relationship(back_populates="feed")


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (UniqueConstraint("feed_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("rss_feeds.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1000))
    source_name: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    feed: Mapped[RssFeed] = relationship(back_populates="articles")
    markets: Mapped[list["NewsArticleMarket"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class NewsArticleMarket(Base):
    __tablename__ = "news_article_markets"
    __table_args__ = (UniqueConstraint("article_id", "market"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)

    article: Mapped[NewsArticle] = relationship(back_populates="markets")
