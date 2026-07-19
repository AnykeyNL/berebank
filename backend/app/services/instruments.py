"""Curated list of stock and fund instruments served via Twelve Data.

Universe: the AEX (Euronext Amsterdam) constituents, the S&P 100, and a
handful of popular ETFs ("funds"). Every instrument is exposed as a
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
    asset_class: str   # "stock" | "fund"
    currency: str      # native quote currency ("EUR" or "USD")
    exchange: str | None = None  # Twelve Data exchange suffix (None = US)
    alias: str | None = None     # base-asset override when the ticker collides with a crypto

    @property
    def td_symbol(self) -> str:
        """Symbol as sent to / returned by the Twelve Data API."""
        return f"{self.symbol}:{self.exchange}" if self.exchange else self.symbol

    @property
    def base(self) -> str:
        return self.alias or self.symbol

    @property
    def market(self) -> str:
        return f"{self.base}-EUR"


def _nl(symbol: str, asset_class: str = "stock") -> Instrument:
    return Instrument(symbol, asset_class, "EUR", "Euronext")


def _us(symbol: str, asset_class: str = "stock") -> Instrument:
    return Instrument(symbol, asset_class, "USD")


# AEX constituents (Euronext Amsterdam, quoted in EUR). "SHELL" collides
# with a Bitvavo crypto asset, so Shell trades under its global ticker SHEL.
_DUTCH_STOCKS = [
    "ABN", "AD", "ADYEN", "AGN", "AKZA", "ASM", "ASML", "ASRNL", "BESI",
    "DSFIR", "EXO", "HEIA", "IMCD", "INGA", "KPN", "MT", "NN",
    "PHIA", "PRX", "RAND", "REN", "UMG", "UNA", "WKL",
]

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
    "SPG", "TGT", "TMO", "TMUS", "TSLA", "TXN", "UNH", "UNP", "UPS",
    "USB", "V", "VZ", "WFC", "WMT", "XOM",
]

# Popular ETFs, exposed under the "fund" asset class.
_FUNDS_NL = ["IWDA", "VWRL", "VUSA", "EMIM"]
_FUNDS_US = ["SPY", "QQQ"]

INSTRUMENTS: list[Instrument] = (
    [_nl(s) for s in _DUTCH_STOCKS]
    + [Instrument("SHELL", "stock", "EUR", "Euronext", alias="SHEL")]
    + [_us(s) for s in _US_STOCKS]
    + [_nl(s, "fund") for s in _FUNDS_NL]
    + [_us(s, "fund") for s in _FUNDS_US]
)

INSTRUMENTS_BY_MARKET: dict[str, Instrument] = {i.market: i for i in INSTRUMENTS}
