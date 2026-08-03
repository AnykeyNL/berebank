"""Scratch: round-trip the Opus dataset export/import."""
import io
import os
import time

os.environ.setdefault(
    "BEREBANK_DATABASE_URL",
    "sqlite:///C:\\projects\\berebank\\backend\\berebank_opus_smoke.db",
)

from app.database import SessionLocal  # noqa: E402
from app.services import opus_dataset_transfer as transfer  # noqa: E402

db = SessionLocal()
print("status:", transfer.dataset_status(db))

started = time.monotonic()
chunks = list(transfer.export_dataset(db, include_candles=False))
blob = b"".join(chunks)
print(f"export: {time.monotonic() - started:.2f}s chunks={len(chunks)} bytes={len(blob)}")

started = time.monotonic()
big = b"".join(transfer.export_dataset(db, include_candles=True))
print(f"export+candles: {time.monotonic() - started:.2f}s bytes={len(big)}")
db.close()

# Import into a fresh database.
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402

target_engine = create_engine(
    "sqlite:///C:\\projects\\berebank\\backend\\berebank_opus_target.db",
    connect_args={"check_same_thread": False},
)
Base.metadata.create_all(bind=target_engine)
TargetSession = sessionmaker(bind=target_engine, autoflush=False, expire_on_commit=False)
target = TargetSession()
started = time.monotonic()
result = transfer.import_dataset(target, io.BytesIO(big))
print(f"import: {time.monotonic() - started:.2f}s {result}")
print("target status:", transfer.dataset_status(target))

# Idempotency: importing the same file again writes nothing new.
result = transfer.import_dataset(target, io.BytesIO(big))
print("second import:", result)

# Truncated file must fail cleanly rather than corrupt anything.
try:
    transfer.import_dataset(target, io.BytesIO(big[: len(big) // 2]))
except Exception as exc:
    print("truncated:", type(exc).__name__, str(exc)[:120])

# Plain (uncompressed) NDJSON is accepted too.
import gzip  # noqa: E402

plain = gzip.decompress(blob)
result = transfer.import_dataset(target, io.BytesIO(plain))
print("plain import:", result)

try:
    transfer.import_dataset(target, io.BytesIO(b""))
except ValueError as exc:
    print("empty:", exc)
try:
    transfer.import_dataset(target, io.BytesIO(b'{"type":"macro"}\n'))
except ValueError as exc:
    print("no header:", exc)
target.close()
