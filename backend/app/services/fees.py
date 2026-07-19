"""Bitvavo Category A fee tiers (https://bitvavo.com/en/fees).

Tier is determined by the account's trailing 30-day executed trade volume in EUR.
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Trade

# (min 30-day volume EUR, maker fee, taker fee)
FEE_TIERS: list[tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("100000000"), Decimal("0.0000"), Decimal("0.0002")),
    (Decimal("25000000"), Decimal("0.0000"), Decimal("0.0004")),
    (Decimal("10000000"), Decimal("0.0000"), Decimal("0.0008")),
    (Decimal("5000000"), Decimal("0.0001"), Decimal("0.0010")),
    (Decimal("2500000"), Decimal("0.0003"), Decimal("0.0012")),
    (Decimal("1000000"), Decimal("0.0005"), Decimal("0.0014")),
    (Decimal("500000"), Decimal("0.0008"), Decimal("0.0016")),
    (Decimal("250000"), Decimal("0.0009"), Decimal("0.0018")),
    (Decimal("100000"), Decimal("0.0010"), Decimal("0.0020")),
    (Decimal("0"), Decimal("0.0015"), Decimal("0.0025")),
]

FEE_QUANT = Decimal("0.00000001")  # fees rounded up to 8 decimals, like Bitvavo


def get_30d_volume(db: Session, account_id: int) -> Decimal:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    trades = db.scalars(
        select(Trade).where(Trade.account_id == account_id, Trade.created_at >= cutoff)
    ).all()
    return sum((t.eur_value for t in trades), Decimal("0"))


def get_fee_rates(volume_30d: Decimal) -> tuple[Decimal, Decimal]:
    """Return (maker, taker) fee rates for the given 30-day volume."""
    for min_volume, maker, taker in FEE_TIERS:
        if volume_30d >= min_volume:
            return maker, taker
    return FEE_TIERS[-1][1], FEE_TIERS[-1][2]


def calc_fee(eur_value: Decimal, rate: Decimal) -> Decimal:
    return (eur_value * rate).quantize(FEE_QUANT, rounding=ROUND_UP)
