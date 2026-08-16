from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer

from .config import AMOUNT_QUANTUM, MIN_ORDER_EUR


def utc_iso(moment: datetime | None) -> str | None:
    """A timestamp with an explicit offset, whatever the database returned.

    Everything is stored in UTC, but SQLite hands back naive datetimes where
    PostgreSQL keeps the zone. A client should not have to know which one it
    is talking to.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---- Auth ----

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    preferred_language: str | None = Field(default=None, pattern="^(en|nl)$")
    mcp_trading_enabled: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    role: str
    is_active: bool
    preferred_language: str | None = None
    mcp_trading_enabled: bool = False


# ---- Markets ----

class MarketOut(BaseModel):
    market: str
    base: str
    quote: str
    name: str | None = None  # full asset name, e.g. "Apple Inc." or "Bitcoin"
    listing: str | None = None  # venue, e.g. "Bitvavo", "NASDAQ", "NYSE"
    asset_class: str = "crypto"  # crypto | stock | fund | commodity
    market_open: bool | None = None  # None for crypto (always open)
    # From the trading calendar, UTC ISO 8601; null for crypto. market_open
    # stays the live truth (it sees halts); these are the schedule.
    next_open: str | None = None
    next_close: str | None = None
    last: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    open: Decimal | None = None
    change_24h_pct: Decimal | None = None
    volume_quote: Decimal | None = None
    has_news: bool = False
    # Order sizing rules, so clients do not have to guess. tick_size,
    # amount_decimals and min_order_base are the venue's own (Bitvavo; null for
    # the Twelve Data instruments). amount_quantum and min_order_eur are what
    # this engine enforces on every market.
    tick_size: str | None = None
    amount_decimals: int | None = None
    min_order_base: str | None = None
    amount_quantum: str = AMOUNT_QUANTUM
    min_order_eur: Decimal = Decimal(MIN_ORDER_EUR)


class NewsItemOut(BaseModel):
    id: str
    datetime: str
    title: str
    body: str
    language: list[str] = []
    url: str | None = None
    source: str | None = None


class NewsPageOut(BaseModel):
    items: list[NewsItemOut]
    page: int
    page_size: int
    total_pages: int
    total_count: int


# ---- Orders / trades ----

class OrderCreate(BaseModel):
    market: str
    side: str = Field(pattern="^(buy|sell)$")
    order_type: str = Field(pattern="^(market|limit|stop_loss)$")
    amount: Decimal | None = Field(default=None, gt=0)
    amount_quote: Decimal | None = Field(default=None, gt=0)  # EUR, market orders only
    limit_price: Decimal | None = Field(default=None, gt=0)
    trigger_price: Decimal | None = Field(default=None, gt=0)  # stop-loss orders only
    client_order_id: str | None = Field(default=None, max_length=64)  # replay guard
    # Resting orders only. expires_in_sessions counts trading sessions, which
    # is the only reading that survives a weekend.
    time_in_force: str | None = Field(default=None, pattern="^(gtc|day|gtd)$")
    expires_at: datetime | None = None
    expires_in_sessions: int | None = Field(default=None, ge=1)


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_order_id: str | None = None
    market: str
    side: str
    order_type: str
    status: str  # open | filled | cancelled | expired
    amount: Decimal | None
    amount_quote: Decimal | None
    limit_price: Decimal | None
    trigger_price: Decimal | None
    fee_paid: Decimal | None
    filled_price: Decimal | None
    time_in_force: str = "gtc"
    expires_at: datetime | None = None
    expires_after_sessions: int | None = None
    created_at: datetime
    filled_at: datetime | None

    @field_serializer("expires_at", "created_at", "filled_at")
    def _as_utc(self, moment: datetime | None) -> str | None:
        return utc_iso(moment)


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    market: str
    side: str
    amount: Decimal
    price: Decimal
    eur_value: Decimal
    fee_eur: Decimal
    created_at: datetime


class TradePnlOut(TradeOut):
    """Trade enriched with FIFO profit/loss data (sell trades only)."""

    pnl_eur: Decimal | None = None
    pnl_pct: Decimal | None = None
    held_seconds: float | None = None


# ---- Portfolio ----

class HoldingOut(BaseModel):
    asset: str
    amount: Decimal  # available (not reserved) amount
    reserved: Decimal = Decimal("0")  # amount locked in open limit sell orders
    market: str | None
    name: str | None = None
    listing: str | None = None
    current_price: Decimal | None
    eur_value: Decimal | None  # values amount + reserved at the live price


class PortfolioOut(BaseModel):
    balance_eur: Decimal
    reserved_eur: Decimal
    holdings: list[HoldingOut]
    holdings_value_eur: Decimal
    total_value_eur: Decimal
    fee_tier: "FeeTierOut"


class FeeTierOut(BaseModel):
    volume_30d_eur: Decimal
    maker_pct: Decimal
    taker_pct: Decimal


class PortfolioSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    total_value_eur: Decimal
    asset_count: int


# ---- Leaderboard ----

class LeaderboardEntry(BaseModel):
    user_id: int
    display_name: str
    trades: int
    cash_eur: Decimal  # balance + EUR reserved for open limit buys
    assets_eur: Decimal  # holdings valued at the live last price
    total_eur: Decimal


class LeaderboardHistoryPoint(BaseModel):
    """One moment in the competition, from the account-value snapshots."""

    created_at: datetime
    rank: int
    total_eur: Decimal
    leader_total_eur: Decimal
    traders: int

    @field_serializer("created_at")
    def _as_utc(self, moment: datetime) -> str | None:
        return utc_iso(moment)


class LeaderboardHistoryOut(BaseModel):
    days: int
    interval: str
    points: list[LeaderboardHistoryPoint]


# ---- Admin ----

class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    display_name: str
    role: str = Field(default="user", pattern="^(user|bank_manager)$")
    initial_balance_eur: Decimal = Field(default=Decimal("0"), ge=0)


class AdminUserUpdate(BaseModel):
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=6)
    role: str | None = Field(default=None, pattern="^(user|bank_manager)$")
    is_active: bool | None = None
    balance_eur: Decimal | None = Field(default=None, ge=0)


class AdminUserOut(UserOut):
    balance_eur: Decimal
    created_at: datetime


class SettingsOut(BaseModel):
    bitvavo_api_key_masked: str | None
    has_api_secret: bool
    connection: dict
    twelvedata_api_key_masked: str | None = None
    twelvedata: dict = {}
    coinglass_api_key_masked: str | None = None
    coinglass: dict = {}


class SettingsUpdate(BaseModel):
    bitvavo_api_key: str | None = None
    bitvavo_api_secret: str | None = None
    twelvedata_api_key: str | None = None
    coinglass_api_key: str | None = None


class RssFeedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    name: str
    enabled: bool
    last_fetched_at: datetime | None
    last_error: str | None
    created_at: datetime


class RssFeedCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=100)


class RssFeedUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class RssFeedStatusOut(BaseModel):
    feeds: list[RssFeedOut]
    aggregator: dict


class CandleHistoryStatusOut(BaseModel):
    market_count: int
    candle_count: int
    first_day: str | None
    last_day: str | None
    gtp56sol_deep_markets: int
    last_harvest: datetime | None = None


class CandleHistoryImportOut(BaseModel):
    markets_imported: int
    rows_written: int
    settings_imported: int
    skipped_invalid: int


class OpusDatasetStatusOut(BaseModel):
    macro_rows: int
    macro_series: int
    macro_first_day: str | None
    macro_last_day: str | None
    calibration_rows: int
    calibrated_at: datetime | None
    recommendation_rows: int
    recommendations_evaluated: int
    recommendation_first_day: str | None
    recommendation_last_day: str | None
    last_harvest: datetime | None = None
    harvest_error: str | None = None


class OpusDatasetImportOut(BaseModel):
    macro_rows: int
    macro_records: int
    calibration_rows: int
    recommendation_rows: int
    candle_rows: int
    skipped_invalid: int


class OpusRecalibrateOut(BaseModel):
    markets: int
    rows: int
    seconds: float
