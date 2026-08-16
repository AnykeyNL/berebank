"""Background sweeper that closes resting orders past their expiry.

A hook in the price matcher would not be enough. Stocks and funds stop ticking
the moment their exchange closes, and that is exactly when a day order has to
expire; commodities do the same over the weekend. So this runs on the clock
rather than on price updates.
"""
import asyncio
import logging
from datetime import datetime, timezone

from ..database import SessionLocal
from .trading import expire_due_orders, trade_lock

logger = logging.getLogger("berebank.order_expiry")

SWEEP_INTERVAL = 60  # seconds
STARTUP_DELAY = 10


class OrderExpiryService:
    def __init__(self) -> None:
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self.expired_total = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="order-expiry")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(STARTUP_DELAY)
        while True:
            await self._sweep_once()
            await asyncio.sleep(SWEEP_INTERVAL)

    async def _sweep_once(self) -> None:
        # The same lock the matcher and the order routes take, so a fill and an
        # expiry can never touch one order at the same time.
        async with trade_lock:
            db = SessionLocal()
            try:
                expired = expire_due_orders(db)
                self.expired_total += len(expired)
                self.last_run = datetime.now(timezone.utc)
                self.last_error = None
                if expired:
                    logger.info("Expired %d order(s)", len(expired))
            except Exception as exc:
                logger.exception("Order expiry sweep failed: %s", exc)
                self.last_error = str(exc)
                db.rollback()
            finally:
                db.close()


order_expiry_service = OrderExpiryService()
