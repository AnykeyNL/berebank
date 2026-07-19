from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
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
    order_type: Mapped[str] = mapped_column(String(6))  # market | limit
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | filled | cancelled
    amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # base asset amount
    amount_quote: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # EUR amount (market orders)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
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
