"""Bitvavo market data client.

Fetches the list of EUR markets over REST, then keeps a live price cache
updated via the public WebSocket API (ticker24h channel). Public market data
requires no API key. Reconnects automatically when the connection drops.
"""
import asyncio
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable

import httpx
import websockets

from ..config import BITVAVO_REST_URL, BITVAVO_WS_URL

logger = logging.getLogger("berebank.bitvavo")

PriceListener = Callable[[list[dict]], Awaitable[None]]


def _dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class BitvavoService:
    def __init__(self) -> None:
        # market -> {"base", "quote", "min_quote"}
        self.markets: dict[str, dict] = {}
        # market -> {"last", "bid", "ask", "open", "volume_quote", "timestamp"}
        self.prices: dict[str, dict] = {}
        self.connected = False
        self.last_update: float | None = None
        self._listeners: list[PriceListener] = []
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    # ---- lifecycle ----

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="bitvavo-ws")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def add_listener(self, listener: PriceListener) -> None:
        """Register an async callback invoked with each batch of ticker updates."""
        self._listeners.append(listener)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # ---- data access ----

    def get_price(self, market: str) -> dict | None:
        return self.prices.get(market)

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "markets": len(self.markets),
            "prices_cached": len(self.prices),
            "last_update": self.last_update,
        }

    # ---- internals ----

    async def _run(self) -> None:
        while True:
            try:
                await self._load_markets()
                await self._prefill_prices()
                await self._ws_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Bitvavo connection error: %s; retrying in 5s", exc)
            self.connected = False
            await asyncio.sleep(5)

    async def _load_markets(self) -> None:
        async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
            markets_resp = await client.get("/markets")
            markets_resp.raise_for_status()
            assets_resp = await client.get("/assets")
            assets_resp.raise_for_status()
            names = {a["symbol"]: a["name"] for a in assets_resp.json()}
            markets = {}
            for m in markets_resp.json():
                if m.get("quote") == "EUR" and m.get("status") == "trading":
                    markets[m["market"]] = {
                        "base": m["base"],
                        "quote": m["quote"],
                        "min_quote": _dec(m.get("minOrderInQuoteAsset")),
                        "name": names.get(m["base"]),
                        "listing": "Bitvavo",
                    }
            if markets:
                self.markets = markets
            logger.info("Loaded %d EUR markets from Bitvavo", len(markets))

    async def _prefill_prices(self) -> None:
        async with httpx.AsyncClient(base_url=BITVAVO_REST_URL, timeout=15) as client:
            resp = await client.get("/ticker/24h")
            resp.raise_for_status()
            updates = self._apply_ticker_data(resp.json())
            logger.info("Prefilled %d market prices", len(updates))

    async def _ws_loop(self) -> None:
        market_names = list(self.markets.keys())
        async with websockets.connect(BITVAVO_WS_URL, ping_interval=20) as ws:
            await ws.send(json.dumps({
                "action": "subscribe",
                "channels": [{"name": "ticker24h", "markets": market_names}],
            }))
            self.connected = True
            logger.info("Bitvavo WebSocket connected, subscribed to %d markets", len(market_names))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("event") == "ticker24h":
                    updates = self._apply_ticker_data(msg.get("data", []))
                    if updates:
                        await self._notify(updates)
                elif "error" in msg:
                    logger.warning("Bitvavo WS error message: %s", msg)

    def _apply_ticker_data(self, data: list[dict]) -> list[dict]:
        updates = []
        for item in data:
            market = item.get("market")
            if market not in self.markets:
                continue
            entry = {
                "market": market,
                "last": _dec(item.get("last")),
                "bid": _dec(item.get("bid")),
                "ask": _dec(item.get("ask")),
                "open": _dec(item.get("open")),
                "volume_quote": _dec(item.get("volumeQuote")),
                "timestamp": item.get("timestamp"),
            }
            self.prices[market] = entry
            updates.append(entry)
        if updates:
            loop = asyncio.get_event_loop()
            self.last_update = loop.time()
        return updates

    async def _notify(self, updates: list[dict]) -> None:
        for listener in self._listeners:
            try:
                await listener(updates)
            except Exception:
                logger.exception("Price listener failed")
        for q in list(self._subscribers):
            try:
                q.put_nowait(updates)
            except asyncio.QueueFull:
                pass


bitvavo_service = BitvavoService()
