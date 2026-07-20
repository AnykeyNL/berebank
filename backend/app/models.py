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
