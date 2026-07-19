import asyncio
import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import ADMIN_EMAIL, ADMIN_PASSWORD, CORS_ORIGINS
from .database import Base, SessionLocal, engine
from .models import Account, User
from .routers import admin, auth, markets, orders, portfolio
from .security import hash_password
from .services.bitvavo import bitvavo_service
from .services.trading import load_open_limit_markets, match_limit_orders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("berebank")


def seed_bank_manager() -> None:
    db = SessionLocal()
    try:
        exists = db.scalar(select(User).where(User.role == "bank_manager"))
        if exists is None:
            user = User(
                email=ADMIN_EMAIL.lower(),
                password_hash=hash_password(ADMIN_PASSWORD),
                display_name="Bank Manager",
                role="bank_manager",
            )
            db.add(user)
            db.flush()
            db.add(Account(user_id=user.id, balance_eur=Decimal("0")))
            db.commit()
            logger.info("Seeded BankManager account: %s (password from BEREBANK_ADMIN_PASSWORD)", ADMIN_EMAIL)
    finally:
        db.close()


async def _limit_order_listener(updates: list[dict]) -> None:
    await match_limit_orders(updates, SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_bank_manager()
    db = SessionLocal()
    try:
        load_open_limit_markets(db)
    finally:
        db.close()
    bitvavo_service.add_listener(_limit_order_listener)
    bitvavo_service.start()
    yield
    await bitvavo_service.stop()


app = FastAPI(title="de BereBank", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(markets.router)
app.include_router(orders.router)
app.include_router(portfolio.router)
app.include_router(admin.router)


def _price_payload(entry: dict) -> dict:
    out = {}
    for key, value in entry.items():
        out[key] = str(value) if isinstance(value, Decimal) else value
    return out


@app.websocket("/ws/prices")
async def ws_prices(websocket: WebSocket):
    """Stream live ticker updates to the frontend, throttled to ~1 batch/second."""
    await websocket.accept()
    queue = bitvavo_service.subscribe()
    try:
        snapshot = [_price_payload(p) for p in bitvavo_service.prices.values()]
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snapshot}))
        while True:
            pending: dict[str, dict] = {}
            batch = await queue.get()
            for entry in batch:
                pending[entry["market"]] = entry
            # Drain whatever accumulates within a second into one message.
            await asyncio.sleep(1)
            while not queue.empty():
                for entry in queue.get_nowait():
                    pending[entry["market"]] = entry
            await websocket.send_text(json.dumps({
                "type": "update",
                "data": [_price_payload(e) for e in pending.values()],
            }))
    except WebSocketDisconnect:
        pass
    finally:
        bitvavo_service.unsubscribe(queue)


@app.get("/health")
def health():
    return {"status": "ok", "bitvavo": bitvavo_service.status()}
