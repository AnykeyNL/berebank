"""Hourly portfolio value snapshots for the portfolio chart.

Records total account value and unique asset count for every active
non-admin user, and prunes snapshots older than the retention window.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Account, PortfolioSnapshot, User, utcnow
from .market_data import market_data_service
from .valuation import compute_account_valuations

logger = logging.getLogger("berebank.snapshots")

RETENTION_DAYS = 180
STARTUP_DELAY = 60  # seconds; let the live price feeds populate first


def record_snapshots(db: Session, now: datetime | None = None) -> int:
    """Snapshot all active non-admin accounts and prune old rows."""
    now = now or utcnow()
    rows = db.execute(
        select(Account.id, Account.balance_eur)
        .join(User, User.id == Account.user_id)
        .where(User.is_active, User.role != "bank_manager")
    ).all()

    valuations = compute_account_valuations(db, dict(rows))
    for account_id, valuation in valuations.items():
        db.add(PortfolioSnapshot(
            account_id=account_id,
            total_value_eur=valuation.total_eur,
            asset_count=valuation.asset_count,
            created_at=now,
        ))

    db.execute(
        delete(PortfolioSnapshot).where(
            PortfolioSnapshot.created_at < now - timedelta(days=RETENTION_DAYS)
        ),
        execution_options={"synchronize_session": False},
    )
    db.commit()
    return len(valuations)


class PortfolioSnapshotService:
    def __init__(self) -> None:
        self.last_run: datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="portfolio-snapshots")

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
            self._snapshot_once()
            await asyncio.sleep(self._seconds_until_next_hour())

    def _snapshot_once(self) -> None:
        # Without any live prices a snapshot would record cash-only dips.
        if not market_data_service.snapshot():
            logger.warning("Skipping portfolio snapshot: no live prices available yet")
            return
        db = SessionLocal()
        try:
            count = record_snapshots(db)
            self.last_run = datetime.now(timezone.utc)
            self.last_error = None
            logger.info("Recorded portfolio snapshots for %d accounts", count)
        except Exception as exc:
            logger.exception("Portfolio snapshot failed: %s", exc)
            self.last_error = str(exc)
        finally:
            db.close()

    @staticmethod
    def _seconds_until_next_hour() -> float:
        now = datetime.now(timezone.utc)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return max((next_hour - now).total_seconds(), 1.0)


portfolio_snapshot_service = PortfolioSnapshotService()
