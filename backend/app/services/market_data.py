"""Unified market data facade over the Bitvavo (crypto) and Twelve Data
(stocks/funds) feeds.

Markets from both sources share the ``{BASE}-EUR`` naming convention. On a
symbol collision the crypto market wins and the stock/fund instrument is
hidden. Consumers (trading engine, REST routers, price WebSocket) use this
facade instead of the individual services.
"""
import asyncio
import logging

from .bitvavo import PriceListener, bitvavo_service
from .twelvedata import twelvedata_service

logger = logging.getLogger("berebank.market_data")


class MarketDataService:
    def __init__(self) -> None:
        self._listeners: list[PriceListener] = []
        self._subscribers: set[asyncio.Queue] = set()
        bitvavo_service.add_listener(self._on_updates)
        twelvedata_service.add_listener(self._on_twelvedata_updates)

    # ---- market registry ----

    @property
    def markets(self) -> dict[str, dict]:
        merged = {
            market: {**info, "asset_class": "crypto"}
            for market, info in bitvavo_service.markets.items()
        }
        for market, info in twelvedata_service.markets.items():
            if market not in merged:
                merged[market] = info
        return merged

    def get_market(self, market: str) -> dict | None:
        info = bitvavo_service.markets.get(market)
        if info is not None:
            return {**info, "asset_class": "crypto"}
        return twelvedata_service.markets.get(market)

    def get_price(self, market: str) -> dict | None:
        # Crypto wins on collisions, mirroring the market registry.
        if market in bitvavo_service.markets:
            return bitvavo_service.get_price(market)
        return twelvedata_service.get_price(market)

    def snapshot(self) -> list[dict]:
        entries = dict(twelvedata_service.prices)
        entries.update(bitvavo_service.prices)
        return list(entries.values())

    # ---- update fan-out ----

    def add_listener(self, listener: PriceListener) -> None:
        self._listeners.append(listener)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _on_twelvedata_updates(self, updates: list[dict]) -> None:
        # Hide instruments whose ticker collides with a crypto market.
        filtered = [u for u in updates if u["market"] not in bitvavo_service.markets]
        if filtered:
            await self._on_updates(filtered)

    async def _on_updates(self, updates: list[dict]) -> None:
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


market_data_service = MarketDataService()
