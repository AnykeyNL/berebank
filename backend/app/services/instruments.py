"""Curated list of stock, fund and commodity instruments served via Twelve Data.

Universe: the S&P 100, a handful of popular ETFs ("funds") and the spot
commodities Twelve Data offers (precious metals and oil). Every
instrument is exposed as a
``{TICKER}-EUR`` market so the rest of the app (portfolio, leaderboard,
orders) can treat the ticker exactly like a crypto base asset. USD-priced
instruments are converted to EUR with the live USD/EUR rate.

Keep this list free of tickers containing "-" (the market separator).
Collisions with Bitvavo crypto base assets are resolved at runtime: the
crypto market wins and the instrument is skipped.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str        # exchange ticker as known by Twelve Data
    asset_class: str   # "stock" | "fund" | "commodity"
    currency: str      # native quote currency ("EUR" or "USD")
    exchange: str | None = None  # Twelve Data exchange suffix (None = US)
    alias: str | None = None     # base-asset override when the ticker collides with a crypto
    name: str | None = None      # display-name override (quote names are kept otherwise)

    @property
    def td_symbol(self) -> str:
        """Symbol as sent to / returned by the Twelve Data API."""
        # Commodities are slash pairs against a quote currency (e.g. XAU/EUR,
        # WTI/USD); stocks and funds are plain tickers with an optional
        # exchange suffix.
        if self.asset_class == "commodity":
            return f"{self.symbol}/{self.currency}"
        return f"{self.symbol}:{self.exchange}" if self.exchange else self.symbol

    @property
    def base(self) -> str:
        return self.alias or self.symbol

    @property
    def market(self) -> str:
        return f"{self.base}-EUR"


def _us(symbol: str, asset_class: str = "stock") -> Instrument:
    return Instrument(symbol, asset_class, "USD")


# S&P 100 (class-share duplicates and dotted tickers omitted). C, CAT, CVX,
# F, MET and T are excluded: those tickers are crypto assets on Bitvavo and
# the crypto market wins on collisions.
_US_STOCKS = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMD", "AMGN", "AMT",
    "AMZN", "AVGO", "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY",
    "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO",
    "CVS", "DE", "DHR", "DIS", "DOW", "DUK", "EMR", "FDX",
    "GD", "GE", "GILD", "GM", "GOOGL", "GS", "HD", "HON", "IBM", "INTC",
    "INTU", "ISRG", "JNJ", "JPM", "KHC", "KO", "LIN", "LLY", "LMT", "LOW",
    "MA", "MCD", "MDLZ", "MDT", "META", "MMM", "MO", "MRK", "MS",
    "MSFT", "NEE", "NFLX", "NKE", "NOW", "NVDA", "ORCL", "PEP", "PFE",
    "PG", "PLTR", "PM", "PYPL", "QCOM", "RTX", "SBUX", "SCHW", "SO",
    "SPCX", "SPG", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP",
    "UPS", "USB", "V", "VZ", "WFC", "WMT", "XOM",
]

# Popular US ETFs, exposed under the "fund" asset class.
_FUNDS_US = [
    # Broad US
    "SPY", "QQQ", "VOO", "VTI", "IWM",
    # International
    "VEA", "VWO",
    # Bonds
    "BND", "TLT",
    # Dividend
    "SCHD",
    # Tech
    "XLK", "VGT", "SOXX", "IGV", "ARKK",
    # Bitcoin & commodities
    "IBIT", "GLD",
]

# Spot commodities. XAU and XAG are quoted natively in EUR; the rest are
# USD spot prices converted with the live USD/EUR rate, like US stocks.
# These trade on forex hours (roughly 24/5, closed on weekends).
_COMMODITIES = [
    Instrument("XAU", "commodity", "EUR", name="Gold"),
    Instrument("XAG", "commodity", "EUR", name="Silver"),
    Instrument("XPT", "commodity", "USD", name="Platinum"),
    Instrument("XPD", "commodity", "USD", name="Palladium"),
    Instrument("WTI", "commodity", "USD", name="WTI Crude Oil"),
    Instrument("XBR", "commodity", "USD", name="Brent Crude Oil"),
    Instrument("URALS", "commodity", "USD", name="Urals Crude Oil"),
]

INSTRUMENTS: list[Instrument] = (
    [_us(s) for s in _US_STOCKS]
    + [_us(s, "fund") for s in _FUNDS_US]
    + _COMMODITIES
)

INSTRUMENTS_BY_MARKET: dict[str, Instrument] = {i.market: i for i in INSTRUMENTS}
