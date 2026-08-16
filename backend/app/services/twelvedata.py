"""Twelve Data market data client for stocks, funds and commodities.

Polls batched /quote requests for the curated instrument list (see
instruments.py) once per minute, plus the USD/EUR exchange rate used to
convert USD-priced instruments to EUR. The service is idle until a
BankManager saves a Twelve Data API key in the admin settings.

Exposes the same surface as BitvavoService (markets, prices, get_price,
status, add_listener) so the market_data facade can merge both feeds.
"""
import asyncio
import calendar
import logging
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable

import httpx

from .instruments import INSTRUMENTS, Instrument

logger = logging.getLogger("berebank.twelvedata")

TWELVEDATA_REST_URL = "https://api.twelvedata.com"
POLL_INTERVAL = 60  # seconds; ~130 API credits/minute on the Pro plan
QUOTE_CHUNK_SIZE = 40

PriceListener = Callable[[list[dict]], Awaitable[None]]


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _listing_from_quote(quote: dict) -> str | None:
    """Map Twelve Data quote metadata to a human-readable exchange name."""
    exchange = quote.get("exchange")
    if not exchange:
        return None
    return exchange


def _end_date_param(end_ms: int) -> str:
    """Twelve Data ``end_date`` for an exclusive epoch-ms bound.

    Their bound is inclusive, so step back a second; bars landing exactly on
    the boundary are dropped in ``_rows_to_candles``.
    """
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(end_ms // 1000 - 1))


def _rows_to_candles(rows: list[dict], fx: Decimal, end_ms: int | None = None) -> list[list]:
    """time_series rows -> API-shape candles in EUR, oldest first.

    Bars at or after ``end_ms`` are dropped so the exclusive bound holds even
    though Twelve Data's ``end_date`` is inclusive.
    """
    candles = []
    for row in rows:
        raw = row["datetime"]
        fmt = "%Y-%m-%d %H:%M:%S" if " " in raw else "%Y-%m-%d"
        ts = calendar.timegm(time.strptime(raw[:19], fmt)) * 1000  # datetimes are UTC
        if end_ms is not None and ts >= end_ms:
            continue
        candles.append([
            int(ts),
            str(_dec(row["open"]) * fx),
            str(_dec(row["high"]) * fx),
            str(_dec(row["low"]) * fx),
            str(_dec(row["close"]) * fx),
            row.get("volume", "0"),
        ])
    candles.sort(key=lambda c: c[0])
    return candles


class TwelveDataService:
    def __init__(self) -> None:
        # market -> {"base", "quote", "min_quote", "asset_class", "currency"}
        self.markets: dict[str, dict] = {}
        # market -> {"last", "bid", "ask", "open", "volume_quote", "timestamp", "market_open"}
        self.prices: dict[str, dict] = {}
        self.api_key: str | None = None
        self.connected = False
        self.error: str | None = None
        self.last_update: float | None = None
        self.usd_eur: Decimal | None = None
        self._listeners: list[PriceListener] = []
        self._task: asyncio.Task | None = None
        self._instruments: dict[str, Instrument] = {}
        self._warned_symbols: set[str] = set()

    # ---- lifecycle ----

    def start(self, api_key: str | None) -> None:
        """Start polling if an API key is available; otherwise stay idle."""
        self.api_key = (api_key or "").strip() or None
        if self.api_key is None:
            logger.info("Twelve Data API key not configured; stock/fund feed idle")
            return
        self._load_markets()
        self._task = asyncio.create_task(self._run(), name="twelvedata-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    async def restart(self, api_key: str | None) -> None:
        """Apply a new API key immediately (called when admin settings change)."""
        await self.stop()
        self.error = None
        self.start(api_key)

    def add_listener(self, listener: PriceListener) -> None:
        self._listeners.append(listener)

    # ---- data access ----

    def get_price(self, market: str) -> dict | None:
        return self.prices.get(market)

    def status(self) -> dict:
        return {
            "configured": self.api_key is not None,
            "connected": self.connected,
            "markets": len(self.markets),
            "prices_cached": len(self.prices),
            "last_update": self.last_update,
            "usd_eur": str(self.usd_eur) if self.usd_eur is not None else None,
            "error": self.error,
        }

    # ---- internals ----

    def _load_markets(self) -> None:
        self._instruments = {}
        markets: dict[str, dict] = {}
        for inst in INSTRUMENTS:
            self._instruments[inst.market] = inst
            markets[inst.market] = {
                "base": inst.base,
                "quote": "EUR",
                "min_quote": None,
                # Twelve Data quotes no venue sizing rules; only the engine's
                # own 8-decimal amount quantum and EUR 5 minimum apply.
                "min_base": None,
                "tick_size": None,
                "amount_decimals": None,
                "asset_class": inst.asset_class,
                "currency": inst.currency,
                "listing": None,
            }
            if inst.name:
                markets[inst.market]["name"] = inst.name
        self.markets = markets
        logger.info("Loaded %d Twelve Data instruments", len(markets))

    async def _run(self) -> None:
        while True:
            try:
                await self._poll()
                self.connected = True
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.error = str(exc)
                logger.warning("Twelve Data poll failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll(self) -> None:
        async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as client:
            await self._fetch_fx(client)
            instruments = list(self._instruments.values())
            updates: list[dict] = []
            for i in range(0, len(instruments), QUOTE_CHUNK_SIZE):
                chunk = instruments[i:i + QUOTE_CHUNK_SIZE]
                updates.extend(await self._fetch_quotes(client, chunk))
        if updates:
            self.last_update = asyncio.get_event_loop().time()
            await self._notify(updates)

    async def _fetch_fx(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            "/exchange_rate", params={"symbol": "USD/EUR", "apikey": self.api_key}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error" or "rate" not in data:
            raise RuntimeError(f"exchange_rate error: {data.get('message', data)}")
        rate = _dec(data["rate"])
        if rate is None or rate <= 0:
            raise RuntimeError(f"invalid USD/EUR rate: {data.get('rate')!r}")
        self.usd_eur = rate

    async def _fetch_quotes(
        self, client: httpx.AsyncClient, chunk: list[Instrument]
    ) -> list[dict]:
        symbols = ",".join(inst.td_symbol for inst in chunk)
        resp = await client.get(
            "/quote", params={"symbol": symbols, "apikey": self.api_key}
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"quote error: {data.get('message', data)}")
        # A single-symbol request returns the quote object directly.
        if len(chunk) == 1:
            data = {chunk[0].td_symbol: data}

        updates = []
        for inst in chunk:
            quote = data.get(inst.td_symbol)
            if not isinstance(quote, dict) or quote.get("status") == "error":
                if inst.td_symbol not in self._warned_symbols:
                    self._warned_symbols.add(inst.td_symbol)
                    message = quote.get("message") if isinstance(quote, dict) else quote
                    logger.warning("No Twelve Data quote for %s: %s", inst.td_symbol, message)
                continue
            entry = self._build_entry(inst, quote)
            if entry is not None:
                name = quote.get("name")
                # Instruments with a curated display name keep it (commodity
                # quote names read like "Gold Spot / Euro").
                if name and inst.name is None:
                    self.markets[inst.market]["name"] = name
                listing = _listing_from_quote(quote)
                if listing:
                    self.markets[inst.market]["listing"] = listing
                self.prices[inst.market] = entry
                updates.append(entry)
        return updates

    def _build_entry(self, inst: Instrument, quote: dict) -> dict | None:
        last = _dec(quote.get("close"))
        prev_close = _dec(quote.get("previous_close"))
        volume = _dec(quote.get("volume"))
        if last is None:
            return None
        if inst.currency == "USD":
            if self.usd_eur is None:
                return None
            last = last * self.usd_eur
            prev_close = prev_close * self.usd_eur if prev_close is not None else None
        ts = quote.get("last_quote_at") or quote.get("timestamp") or int(time.time())
        return {
            "market": inst.market,
            "last": last,
            # No live order book: fill market orders at the last price.
            "bid": last,
            "ask": last,
            # Previous close as the "open" reference makes the displayed
            # change match the standard daily change for equities.
            "open": prev_close,
            "volume_quote": (volume * last) if volume is not None else None,
            "timestamp": int(ts) * 1000,
            "market_open": bool(quote.get("is_market_open", False)),
        }

    async def _notify(self, updates: list[dict]) -> None:
        for listener in self._listeners:
            try:
                await listener(updates)
            except Exception:
                logger.exception("Price listener failed")

    # ---- candles ----

    # UI range → Twelve Data (interval, outputsize). Stocks only have bars
    # during trading hours, so sizes approximate the requested window.
    _RANGE_PARAMS: dict[str, tuple[str, int]] = {
        "1h": ("1min", 60),
        "1d": ("15min", 26),
        "1w": ("1h", 35),
        "30d": ("1day", 22),
        "90d": ("1day", 63),
        "180d": ("1day", 126),
        "365d": ("1day", 250),
    }

    async def fetch_candles(
        self,
        market: str,
        range_: str,
        extra_bars: int = 0,
        end_ms: int | None = None,
    ) -> list[list]:
        """OHLCV candles as [timestamp_ms, open, high, low, close, volume],
        oldest first, converted to EUR — same shape as the Bitvavo candles.

        ``extra_bars`` extends the window backwards at the same interval
        (used as indicator warm-up by the analysis endpoint). ``end_ms``
        (epoch ms, exclusive) returns the page of bars just before it."""
        inst = self._instruments.get(market)
        if inst is None:
            raise RuntimeError(f"Unknown Twelve Data market: {market}")
        if self.api_key is None:
            raise RuntimeError("Twelve Data API key not configured")
        interval, outputsize = self._RANGE_PARAMS[range_]
        outputsize += extra_bars
        params = {
            "symbol": inst.td_symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "UTC",
            "apikey": self.api_key,
        }
        if end_ms is not None:
            params["end_date"] = _end_date_param(end_ms)
        async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as client:
            resp = await client.get("/time_series", params=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            raise RuntimeError(f"time_series error: {data.get('message', data)}")

        fx = self.usd_eur if inst.currency == "USD" else Decimal("1")
        if fx is None:
            raise RuntimeError("USD/EUR rate not available yet")
        return _rows_to_candles(data["values"], fx, end_ms)

    # ---- press releases / news ----

    async def fetch_recent_press_releases(
        self,
        limit_per_market: int = 2,
        *,
        max_markets: int = 25,
        timeout: float = 12,
    ) -> list[dict]:
        """Recent press releases from a capped set of stock/fund markets."""
        if self.api_key is None or not self._instruments:
            return []

        # Commodities have no press releases (the endpoint returns 404).
        markets = [
            market
            for market, inst in self._instruments.items()
            if inst.asset_class != "commodity"
        ][:max_markets]
        sem = asyncio.Semaphore(5)

        async def fetch_one(
            market: str,
            client: httpx.AsyncClient,
        ) -> list[dict]:
            async with sem:
                try:
                    items = await self.fetch_press_releases(
                        market, limit_per_market, client=client
                    )
                except Exception:
                    return []
                return [
                    {**item, "source": "Twelve Data", "url": None}
                    for item in items
                ]

        async def run() -> list[dict]:
            async with httpx.AsyncClient(
                base_url=TWELVEDATA_REST_URL,
                timeout=5,
            ) as client:
                batches = await asyncio.gather(
                    *(fetch_one(m, client) for m in markets)
                )
            return [item for batch in batches for item in batch]

        return await asyncio.wait_for(run(), timeout=timeout)

    async def fetch_press_releases(
        self,
        market: str,
        limit: int = 10,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict]:
        """Recent press releases for a stock or fund market (newest first)."""
        inst = self._instruments.get(market)
        if inst is None:
            raise RuntimeError(f"Unknown Twelve Data market: {market}")
        if self.api_key is None:
            raise RuntimeError("Twelve Data API key not configured")
        size = max(1, min(limit, 10))
        params = {
            "symbol": inst.td_symbol,
            "outputsize": size,
            "language": "en,en-US",
            "apikey": self.api_key,
        }
        if client is not None:
            resp = await client.get("/press_releases", params=params)
        else:
            async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as own:
                resp = await own.get("/press_releases", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error":
            raise RuntimeError(f"press_releases error: {data.get('message', data)}")
        items = []
        for row in data.get("press_releases") or []:
            items.append({
                "id": row.get("id", ""),
                "datetime": row.get("datetime", ""),
                "title": row.get("title", ""),
                "body": row.get("body", ""),
                "language": row.get("language") or [],
            })
        return items

    # ---- supplementary fundamentals / macro ----

    async def _get_json(
        self,
        path: str,
        params: dict,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict:
        if self.api_key is None:
            raise RuntimeError("Twelve Data API key not configured")
        params = {**params, "apikey": self.api_key}
        if client is not None:
            resp = await client.get(path, params=params)
        else:
            async with httpx.AsyncClient(base_url=TWELVEDATA_REST_URL, timeout=30) as own:
                resp = await own.get(path, params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"{path} error: {data.get('message', data)}")
        return data

    async def fetch_symbol_time_series(
        self,
        symbol: str,
        *,
        interval: str = "1day",
        outputsize: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> list[tuple[int, float]]:
        """Daily closes as ``(timestamp_ms, close)`` oldest first."""
        try:
            data = await self._get_json(
                "/time_series",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "outputsize": outputsize,
                    "timezone": "UTC",
                },
                client=client,
            )
        except (httpx.HTTPStatusError, RuntimeError) as exc:
            logger.warning("Twelve Data time_series unavailable for %s: %s", symbol, exc)
            return []
        if "values" not in data:
            return []
        rows: list[tuple[int, float]] = []
        for row in data["values"]:
            raw = row["datetime"]
            fmt = "%Y-%m-%d %H:%M:%S" if " " in raw else "%Y-%m-%d"
            ts = calendar.timegm(time.strptime(raw[:19], fmt))
            close = _dec(row.get("close"))
            if close is not None:
                rows.append((int(ts) * 1000, float(close)))
        rows.sort(key=lambda item: item[0])
        return rows

    async def fetch_first_time_series(
        self,
        symbols: list[str],
        *,
        interval: str = "1day",
        outputsize: int = 30,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[str | None, list[tuple[int, float]]]:
        """Try ``symbols`` in order; return the first symbol with data."""
        for symbol in symbols:
            rows = await self.fetch_symbol_time_series(
                symbol,
                interval=interval,
                outputsize=outputsize,
                client=client,
            )
            if rows:
                return symbol, rows
        return None, []

    async def fetch_bond_symbols(
        self,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> list[str]:
        """Bond tickers from the Twelve Data catalog (plan-dependent)."""
        try:
            data = await self._get_json("/bonds", {}, client=client)
        except (httpx.HTTPStatusError, RuntimeError) as exc:
            logger.warning("Twelve Data bonds catalog unavailable: %s", exc)
            return []
        items = data.get("result", {}).get("list") or []
        return [
            item["symbol"]
            for item in items
            if isinstance(item, dict) and item.get("symbol")
        ]

    async def fetch_next_earnings(
        self,
        symbol: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict | None:
        """Upcoming earnings row for a US ticker, or None."""
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=120)).isoformat()
        calendar_rows = await self.fetch_earnings_calendar(start, end, client=client)
        upcoming: list[dict] = []
        for day_str, rows in calendar_rows.items():
            if day_str < start:
                continue
            for row in rows:
                if row.get("symbol") == symbol:
                    upcoming.append({**row, "date": day_str})
        if not upcoming:
            return None
        upcoming.sort(key=lambda row: row["date"])
        return upcoming[0]

    async def fetch_insider_transactions(
        self,
        symbol: str,
        *,
        outputsize: int = 20,
        client: httpx.AsyncClient | None = None,
    ) -> list[dict]:
        """Recent insider transactions for a US stock (newest first)."""
        data = await self._get_json(
            "/insider_transactions",
            {"symbol": symbol, "outputsize": outputsize},
            client=client,
        )
        items = data.get("insider_transactions") or data.get("transactions") or []
        return items if isinstance(items, list) else []

    async def fetch_earnings_calendar(
        self,
        start_date: str,
        end_date: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, list[dict]]:
        """Earnings grouped by date (``YYYY-MM-DD`` → rows)."""
        try:
            data = await self._get_json(
                "/earnings_calendar",
                {"start_date": start_date, "end_date": end_date},
                client=client,
            )
        except (httpx.HTTPStatusError, RuntimeError) as exc:
            logger.warning("Twelve Data earnings_calendar unavailable: %s", exc)
            return {}
        earnings = data.get("earnings")
        return earnings if isinstance(earnings, dict) else {}


twelvedata_service = TwelveDataService()
